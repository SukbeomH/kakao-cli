"""Web UI — a device console in the browser (stdlib only).

The web UI is console-only: the live device screen in the center (click/drag
→ tap/swipe, keyboard capture → device) and two file managers in a popup
(device storage / the webui host's disk) with copy, upload and download.
Application-specific automation belongs to the embedding application.

A single background Worker owns the device and serializes UI-touching device
access (PNG capture fallback, adb input, ADBKeyBoard text); file listings and
transfers call adb directly from handler threads (adb's server serializes
transports). No third-party deps, no CDN — the page is inlined.
"""
from __future__ import annotations
import json
import math
import os
import queue
import re
import select
import shutil
import socket
import struct
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

from .adb import connect
from .worker import Worker
from .screen import ScreenBuffer, view_to_device
from .scrcpy import CLIP_MAX, ScrcpySession, ensure_server, ScrcpyError
from .scrcpy import VERSION as SCRCPY_VERSION
from .scrcpy import avc_codec_string
from .scrcpy import enc_touch, enc_scroll, enc_cancel_touch, ACTION_DOWN, ACTION_MOVE, ACTION_UP, ACTION_CANCEL
from .video import VideoHub
from .profile import ConsoleProfile, DEFAULT_PROFILE
from . import fmp4
from .fs import (LocalRoot, shq, parse_stat_lines, android_list_cmd,
                 android_parent, unique_name, content_disposition, clean_upload_name,
                 host_allowed)

DEFAULT_MAX_UPLOAD = 2 << 30          # 2 GiB (--max-upload)
UPLOAD_HEADROOM = 64 << 20            # keep this much free after an upload

# Android keycodes the console keyboard may forward (everything printable goes
# through ADBKeyBoard text instead). Anything else is rejected.
KEYCODES = {
    187,        # APP_SWITCH
    3,          # HOME
    4,          # BACK
    19, 20, 21, 22,   # DPAD up/down/left/right (arrow keys)
    61,         # TAB
    66,         # ENTER
    67,         # DEL (backspace)
    92, 93,     # PAGE_UP / PAGE_DOWN
    112,        # FORWARD_DEL
    122, 123,   # MOVE_HOME / MOVE_END
}


class State:
    """What the console page polls: a status line (worker errors etc.).
    `seq` only bumps on a real change so idle polls answer 204."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.status = "starting …"
        self.bridge = None            # ScrcpySession (clipboard/input/video) or None
        self.closed = threading.Event()
        self.hub = None               # VideoHub or None

    def snapshot(self) -> dict:
        b = self.bridge
        clip_seq = b.clip()[0] if b else 0
        with self.lock:
            return {"seq": self.seq, "status": self.status, "clip_seq": clip_seq,
                    "scrcpy": bool(b and b.connected)}

    def bump(self):
        with self.lock:
            self.seq += 1

    def set_status(self, text: str):
        with self.lock:
            if text != self.status:
                self.status = text
                self.seq += 1


class PointerInput:
    """One finger across viewers. A lost tab/request must never leave it down."""
    LEASE_S = 2.0

    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None
        self.target = None
        self.generation = None
        self.last = None
        self.timer = None
        self.touched = 0.0

    def _cancel(self):
        if self.target and self.last and getattr(self.target, "scid", None) == self.generation:
            try:
                self.target.send(enc_cancel_touch(*self.last))
            except Exception:
                pass
        self.owner = self.target = self.last = None
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def _arm(self, delay):
        self.timer = threading.Timer(delay, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def _expire(self):
        with self.lock:
            left = self.LEASE_S - (time.monotonic() - self.touched)
            if self.owner and left > 0:
                self._arm(left)
            else:
                self._cancel()

    def send(self, bridge, gesture, action, x, y, w, h):
        with self.lock:
            if action == ACTION_DOWN:
                self._cancel()
            elif self.owner != gesture:
                raise ValueError("pointer expired or owned by another viewer")
            elif (self.target is not bridge or self.last[2:] != (w, h)
                  or getattr(bridge, "scid", None) != self.generation):
                self._cancel()
                raise ValueError("display changed during gesture")
            try:
                bridge.send(enc_cancel_touch(x, y, w, h) if action == ACTION_CANCEL
                            else enc_touch(action, x, y, w, h))
            except Exception:
                self._cancel()
                raise
            if action in (ACTION_UP, ACTION_CANCEL):
                self.owner = self.target = self.last = None
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
            else:
                self.owner, self.target, self.last = gesture, bridge, (x, y, w, h)
                self.generation = getattr(bridge, "scid", None)
                self.touched = time.monotonic()
                if self.timer is None:
                    self._arm(self.LEASE_S)


def _pump(worker: Worker, state: State):
    while not state.closed.is_set():
        try:
            kind, data = worker.events.get(timeout=1.0)
        except queue.Empty:
            continue
        if kind == "status":
            state.set_status(data)
        # (chat events don't exist in the console-only web UI)


def make_handler(worker: Worker, state: State, tmpdir: str,
                 screen: ScreenBuffer, screen_enabled: bool, fsroot: LocalRoot,
                 max_upload: int = DEFAULT_MAX_UPLOAD, allow_hosts=(), disk_free=None, profile=DEFAULT_PROFILE):
    page = render_page(profile)
    disk_free = disk_free or (lambda d: shutil.disk_usage(d).free)
    # console file transfers run in their own threads (a large push must not
    # freeze the worker's captures/taps); adb's server serializes transports
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()
    pointer = PointerInput()

    def dev():
        return getattr(worker, "dev", None) or getattr(getattr(worker, "d", None), "dev", None)

    def job_set(jid, **kw):
        with jobs_lock:
            jobs.setdefault(jid, {}).update(kw)

    def run_job(jid, fn, *args):
        def target():
            try:
                job_set(jid, state="done", **(fn(*args) or {}))
            except Exception as e:  # surface the reason in the jobs strip
                job_set(jid, state="error", error=str(e))
        threading.Thread(target=target, daemon=True).start()

    def pull_job(src, dst_rel):
        real_dir = fsroot.resolve(dst_rel)
        if not real_dir or not os.path.isdir(real_dir):
            raise ValueError("local target dir not found")
        d = dev()
        if "D" in d.shell("test -d %s && echo D" % shq(src)):
            raise ValueError("files only (folders are not copied)")
        name = src.rstrip("/").rsplit("/", 1)[-1] or "file"
        name = unique_name(name, lambda n: os.path.exists(os.path.join(real_dir, n)))
        target = os.path.join(real_dir, name)
        d.pull(src, target)
        if not os.path.splitext(name)[1]:          # content blobs have no ext
            from .media import _sniff
            ext = _sniff(target)
            if ext != "bin":
                better = unique_name(name + "." + ext,
                                     lambda n: os.path.exists(os.path.join(real_dir, n)))
                os.replace(target, os.path.join(real_dir, better))
                name = better
        return {"name": name, "dst": fsroot.rel(os.path.join(real_dir, name))}

    def _push_file(real, dst_dir):
        d = dev()
        base = dst_dir if dst_dir.endswith("/") else dst_dir + "/"
        name = unique_name(os.path.basename(real),
                           lambda n: "Y" in d.shell("test -e %s && echo Y" % shq(base + n)))
        d.push(real, base + name)
        d.shell("am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d %s"
                % shq("file://" + base + name))
        return {"name": name, "dst": base + name}

    def push_job(src_rel, dst_dir):
        real = fsroot.resolve(src_rel)
        if not real or not os.path.isfile(real):
            raise ValueError("files only (local source not found)")
        return _push_file(real, dst_dir)

    def staged_push_job(staged, dst_dir):
        try:
            return _push_file(staged, dst_dir)
        finally:                                   # staging is temporary
            try:
                os.unlink(staged); os.rmdir(os.path.dirname(staged))
            except OSError:
                pass

    class Handler(BaseHTTPRequestHandler):
        server_version = "android-console"

        def log_message(self, *a):  # silence per-request logging (1s polling)
            pass

        def _host_ok(self) -> bool:
            return host_allowed(self.headers.get("Host", ""), allow_hosts)

        def _console_get_ok(self) -> bool:
            """Gate for console GETs (frames, listings, downloads)."""
            if not screen_enabled:
                self._json(403, {"error": "console disabled"}); return False
            if not self._host_ok():
                self._json(403, {"error": "host not allowed"}); return False
            return True

        def _csrf_ok(self) -> bool:
            # control is state-changing: require same-origin (if an Origin is sent)
            origin = self.headers.get("Origin")
            if origin:
                host = self.headers.get("Host", "")
                from urllib.parse import urlparse
                if urlparse(origin).netloc != host:
                    return False
            return True

        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                since = None
                if "?" in self.path:
                    q = parse_qs(self.path.split("?", 1)[1])
                    if q.get("since"):
                        try:
                            since = int(q["since"][0])
                        except ValueError:
                            since = None
                snap = state.snapshot()
                if since is not None and since == snap["seq"]:
                    self.send_response(204)
                    self.end_headers()
                    return
                self._json(200, snap)
            elif path == "/api/screen/info":
                q = parse_qs(self.path.partition("?")[2])
                if screen_enabled and q.get("peek") != ["1"]:
                    screen.touch()     # PNG polling acquires/renews the capture lease
                seq, frame, w, h = screen.get()
                self._json(200, {"enabled": screen_enabled, "seq": seq,
                                 "w": w, "h": h, "has_frame": bool(frame)})
            elif path == "/api/screen/frame":
                if not self._console_get_ok():
                    return
                screen.touch()      # mark a viewer so the worker captures
                end = time.time() + 3.0
                while not screen.get()[1] and time.time() < end:
                    time.sleep(0.1)
                _seq, frame, _w, _h = screen.get()
                if not frame:
                    return self._json(503, {"error": "no frame yet"})
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(frame)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(frame)
            elif path.startswith("/api/fs/"):
                self._fs_get(path)
            elif path == "/api/video/info":
                if not self._console_get_ok():
                    return
                h = state.hub
                self._json(200, h.info() if h else {"available": False})
            elif path == "/api/video/stream":
                if not self._console_get_ok():
                    return
                self._video_stream()
            elif path == "/api/clip":
                if not self._console_get_ok():
                    return
                b = state.bridge
                seq, text = b.clip() if b else (0, "")
                self._json(200, {"seq": seq, "text": text, "available": bool(b and b.connected)})
            else:
                self._json(404, {"error": "not found"})

        def _fs_get(self, path):
            if not self._console_get_ok():
                return
            q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            arg = (q.get("path") or [""])[0]
            if path == "/api/fs/local/get":
                real = fsroot.resolve(arg)
                if not real or not os.path.isfile(real):
                    return self._json(404, {"error": "not found or outside root"})
                return self._serve_file(real, os.path.basename(real), "application/octet-stream")
            if path == "/api/fs/android/get":
                return self._android_get(arg)
            if path == "/api/fs/android":
                p = arg or "/sdcard/"
                if not p.startswith("/"):
                    return self._json(400, {"error": "absolute path required"})
                d = dev()
                if d is None:
                    return self._json(503, {"error": "no device"})
                out = d.shell(android_list_cmd(p))
                items = parse_stat_lines(out)
                errs = [l for l in out.splitlines() if l.startswith(("find:", "stat:"))]
                self._json(200, {"path": p if p.endswith("/") else p + "/",
                                 "parent": android_parent(p), "items": items,
                                 "error": errs[0] if (errs and not items) else None})
            elif path == "/api/fs/local":
                items = fsroot.list(arg)
                if items is None:
                    return self._json(404, {"error": "not found or outside root"})
                real = fsroot.resolve(arg)
                parent = fsroot.rel(os.path.dirname(real)) if real != fsroot.root else None
                self._json(200, {"path": fsroot.rel(real), "parent": parent,
                                 "root": fsroot.root, "items": items})
            elif path.startswith("/api/fs/job/"):
                with jobs_lock:
                    j = dict(jobs.get(path[len("/api/fs/job/"):], {}))
                if not j:
                    return self._json(404, {"error": "unknown job"})
                self._json(200, j)
            else:
                self._json(404, {"error": "not found"})

        def _serve_file(self, path, name, mime, attachment=True):
            """Stream a file in 1 MiB chunks (never load it whole)."""
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", content_disposition(name))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def do_POST(self):
            if self.path.startswith("/api/screen/"):
                self._screen_control()
            elif self.path in ("/api/fs/pull", "/api/fs/push"):
                self._fs_transfer()
            elif self.path.split("?", 1)[0] == "/api/fs/upload":
                self._fs_upload()
            elif self.path == "/api/clip":
                self._clip_post()
            elif self.path in ("/api/video/resize", "/api/video/mode", "/api/video/quality", "/api/video/keyframe"):
                self._video_post()
            else:
                self._json(404, {"error": "not found"})

        def _android_get(self, p):
            if not p.startswith("/"):
                return self._json(400, {"error": "absolute path required"})
            d = dev()
            if d is None:
                return self._json(503, {"error": "no device"})
            info = (d.shell("stat -c '%%F|%%s' %s 2>/dev/null" % shq(p)) or "").strip()
            ftype, _, size = info.partition("|")
            if ftype not in ("regular file", "regular empty file") or not size.isdigit():
                return self._json(404, {"error": "not a regular file"})
            proc = d.open_exec_out("cat " + shq(p))
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", size)
                self.send_header("Content-Disposition",
                                 content_disposition(p.rstrip("/").rsplit("/", 1)[-1]))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while True:                      # stream straight from the device
                    chunk = proc.stdout.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass

        def _fs_upload(self):
            """Raw-body upload (one file per request) into the local pane, or —
            with target=android — staged then pushed to the device."""
            if not screen_enabled:
                return self._json(403, {"error": "console disabled"})
            if not self._host_ok():
                return self._json(403, {"error": "host not allowed"})
            if not self._csrf_ok():
                return self._json(403, {"error": "cross-origin blocked"})
            # a custom header forces a CORS preflight (we never answer OPTIONS
            # permissively), so a cross-site form/fetch can't reach this
            if self.headers.get(profile.upload_header) != "upload":
                return self._json(403, {"error": "missing upload operation header"})
            ctype = (self.headers.get("Content-Type") or "").lower()
            if ctype.startswith(("multipart/", "application/x-www-form-urlencoded")):
                return self._json(415, {"error": "raw body only"})
            q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            name = clean_upload_name((q.get("name") or [""])[0])
            if not name:
                return self._json(400, {"error": "bad name"})
            try:
                n = int(self.headers.get("Content-Length") or "")
            except ValueError:
                return self._json(411, {"error": "Content-Length required"})
            if n < 0:
                return self._json(400, {"error": "bad length"})
            if n > max_upload:
                return self._json(413, {"error": "too large (max %d bytes)" % max_upload})
            to_android = (q.get("target") or [""])[0] == "android"
            dir_arg = (q.get("dir") or [""])[0]
            if to_android:
                if not dir_arg.startswith("/"):
                    return self._json(400, {"error": "android dir must be absolute"})
                if dev() is None:
                    return self._json(503, {"error": "no device"})
                target_dir = tempfile.mkdtemp(prefix="stage-", dir=tmpdir)
            else:
                target_dir = fsroot.resolve(dir_arg)
                if not target_dir or not os.path.isdir(target_dir):
                    return self._json(404, {"error": "dir not found or outside root"})
            if disk_free(target_dir) - n < UPLOAD_HEADROOM:
                return self._json(507, {"error": "not enough free space"})
            fd, part = tempfile.mkstemp(dir=target_dir, prefix=".up-", suffix=".part")
            try:
                remaining = n
                with os.fdopen(fd, "wb") as f:
                    while remaining > 0:
                        chunk = self.rfile.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    os.unlink(part)
                    return self._json(400, {"error": "incomplete upload"})
                final = _link_unique(part, target_dir, name)   # atomic, never clobbers
            except BaseException:
                try:
                    os.unlink(part)
                except OSError:
                    pass
                raise
            if to_android:
                import uuid
                jid = uuid.uuid4().hex
                job_set(jid, state="running", op="push", src=final)
                run_job(jid, staged_push_job, os.path.join(target_dir, final), dir_arg)
                return self._json(202, {"id": jid, "name": final})
            self._json(201, {"name": final, "path": fsroot.rel(os.path.join(target_dir, final))})

        def _video_stream(self):
            """Framed binary stream: [type u8][len u32][payload]
               1 meta JSON {w,h,codec,mode} · 2 init (fmp4) / SPS+PPS (annexb)
               3 media: fmp4 moof+mdat, or annexb [key u8][pts u64][access unit]
               4 fmp4 keyframe metadata: {key_time} on the muxer's media timeline."""
            h = state.hub
            if h is None:
                return self._json(503, {"error": "video unavailable (scrcpy not running)"})
            q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            fmt = (q.get("fmt") or ["annexb"])[0]
            if fmt not in ("annexb", "fmp4"):
                return self._json(400, {"error": "fmt must be annexb or fmp4"})
            size = None
            try:
                size = (float(q["w"][0]), float(q["h"][0]), float((q.get("dpr") or ["1"])[0]))
            except (KeyError, ValueError, IndexError):
                pass
            sub = h.subscribe(fmt, size)
            mux = fmp4.Muxer() if fmt == "fmp4" else None
            wh = cfg = None
            ready = False
            def put(t, payload):
                self.wfile.write(struct.pack(">BI", t, len(payload)) + payload)
            try:
                # Keep a stalled TCP receiver from hiding seconds of old video
                # outside the hub's bounded queue, or retaining a handler forever.
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 << 10)
                self.connection.settimeout(2.0)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while True:
                    try:
                        ev = sub.q.get(timeout=2)
                    except queue.Empty:
                        # a static screen produces no frames, so a closed tab is
                        # never noticed by a write — check the socket instead
                        if _peer_closed(self.connection):
                            break
                        continue
                    if ev[0] == "session":
                        wh, ready = (ev[1], ev[2]), False
                        continue
                    if ev[0] == "config":
                        cfg, ready = ev[1], False
                        continue
                    if ev[0] != "frame" or not wh or not cfg:
                        continue
                    _, pts, key, data = ev
                    if not ready:
                        if not key:
                            continue
                        sps, _pps = fmp4.sps_pps(cfg)
                        meta = {"w": wh[0], "h": wh[1], "mode": h.mode,
                                "codec": avc_codec_string(sps) if sps else "avc1.42C029"}
                        put(1, json.dumps(meta).encode())
                        if mux:
                            init = mux.init(cfg, *wh)
                            if init is None:
                                continue
                            put(2, init)
                        else:
                            put(2, cfg)
                        ready = True
                    if mux:
                        key_time = mux.next_dts / fmp4.TIMESCALE
                        frag = mux.frame(pts, key, data)
                        if frag:
                            if key:
                                put(4, json.dumps({"key_time": key_time}).encode())
                            put(3, frag)
                    else:
                        put(3, struct.pack(">BQ", 1 if key else 0, pts) + data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                h.unsubscribe(sub)

        def _video_post(self):
            if not self._guard():
                return
            h = state.hub
            if h is None:
                return self._json(503, {"error": "video unavailable"})
            body = self._read_json()
            try:
                if self.path == "/api/video/keyframe":
                    h._request_key()
                    return self._json(200, {"ok": True})
                if self.path == "/api/video/quality":
                    return self._json(200, h.set_quality(body.get("max_side")))
                if self.path == "/api/video/resize":
                    return self._json(200, h.resize(_num(body.get("w")), _num(body.get("h")),
                                                     _num(body.get("dpr"), 1) or 1))
                return self._json(200, h.set_mode(str(body.get("mode"))))
            except ValueError as e:
                return self._json(400, {"error": str(e)})

        def _clip_post(self):
            """Browser → device clipboard (paste=true also pastes into the focused
            field). Without scrcpy, paste falls back to typing via ADBKeyBoard."""
            if not self._guard():
                return
            body = self._read_json()
            text = body.get("text")
            if not isinstance(text, str) or not text:
                return self._json(400, {"error": "text required"})
            if len(text.encode("utf-8")) > CLIP_MAX:
                return self._json(400, {"error": "too long (max %d bytes)" % CLIP_MAX})
            paste = bool(body.get("paste"))
            b = state.bridge
            if b and b.connected:
                try:
                    ok = b.set_clipboard(text, paste)
                except Exception as e:
                    return self._json(503, {"error": str(e)})
                return self._json(200 if ok else 504, {"ok": ok, "via": "scrcpy"})
            if paste:
                worker.put("screentext", text)
                return self._json(200, {"ok": True, "via": "adbkeyboard"})
            self._json(503, {"error": "device clipboard unavailable (scrcpy not running)"})

        def _guard(self) -> bool:
            """Shared gate for every console control: enabled + JSON + same-origin."""
            if not screen_enabled:
                self._json(403, {"error": "console disabled"}); return False
            if not self._host_ok():
                self._json(403, {"error": "host not allowed"}); return False
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                self._json(415, {"error": "json only"}); return False
            if not self._csrf_ok():
                self._json(403, {"error": "cross-origin blocked"}); return False
            return True

        def _fs_transfer(self):
            if not self._guard():
                return
            if dev() is None:
                return self._json(503, {"error": "no device"})
            body = self._read_json()
            src, dst = str(body.get("src") or ""), str(body.get("dst") or "")
            import uuid
            jid = uuid.uuid4().hex
            if self.path == "/api/fs/pull":           # android file → local dir
                if not src.startswith("/"):
                    return self._json(400, {"error": "android src must be absolute"})
                if fsroot.resolve(dst) is None:
                    return self._json(400, {"error": "local dst outside root"})
                job_set(jid, state="running", op="pull", src=src)
                run_job(jid, pull_job, src, dst)
            else:                                     # local file → android dir
                if fsroot.resolve(src) is None:
                    return self._json(400, {"error": "local src outside root"})
                if not dst.startswith("/"):
                    return self._json(400, {"error": "android dst must be absolute"})
                job_set(jid, state="running", op="push", src=src)
                run_job(jid, push_job, src, dst)
            self._json(202, {"id": jid})

        def _screen_control(self):
            if not self._guard():
                return
            body = self._read_json()
            op = self.path[len("/api/screen/"):]
            _seq, frame, w, h = screen.get()
            hub, bridge = state.hub, state.bridge
            # the viewer watches live video → input goes to the streamed display
            # through scrcpy (video coordinates); a PNG viewer sees display 0 →
            # adb input there (even while another viewer keeps a virtual display)
            live = bool(hub and hub.active and bridge and bridge.connected
                        and bridge.video and hub.wh)
            vid = hub.wh if (live and body.get("src") != "png") else None
            # scrcpy injects keys into its display: only use it when that is the
            # display the viewer sees (video viewer, or no virtual display up)
            vd_up = live and hub.mode == "virtual"
            key_via_scrcpy = bool(bridge and bridge.connected and (vid or not vd_up))
            iw, ih = _num(body.get("iw")), _num(body.get("ih"))
            if not (iw and ih):
                iw, ih = (vid or (w, h))
            if op in ("tap", "swipe", "touch", "scroll") and (not iw or not ih):
                return self._json(409, {"error": "no frame yet"})
            def to_target(cx, cy):
                px, py = view_to_device(_num(cx), _num(cy), _num(body.get("ew")),
                                        _num(body.get("eh")), iw, ih)
                nx, ny = px / max(1, iw - 1), py / max(1, ih - 1)
                tw, th = vid or (w, h)
                return (int(round(nx * (tw - 1))), int(round(ny * (th - 1))))
            if op in ("touch", "scroll"):
                if not vid:
                    return self._json(409, {"error": "live video required"})
                x, y = to_target(body.get("cx"), body.get("cy"))
                try:
                    if op == "scroll":
                        dy = _num(body.get("dy"))
                        if dy:
                            bridge.send(enc_scroll(x, y, *vid, 0, dy))
                    else:
                        actions = {"down": ACTION_DOWN, "move": ACTION_MOVE,
                                   "up": ACTION_UP, "cancel": ACTION_CANCEL}
                        action, gesture = body.get("action"), body.get("gesture")
                        if not isinstance(action, str) or action not in actions or not isinstance(gesture, str) or not 1 <= len(gesture) <= 128:
                            return self._json(400, {"error": "bad pointer event"})
                        pointer.send(bridge, gesture, actions[action], x, y, *vid)
                except ValueError as e:
                    return self._json(409, {"error": str(e)})
                except (ScrcpyError, OSError) as e:
                    return self._json(503, {"error": str(e)})
                return self._json(200, {"ok": True, "via": "scrcpy"})
            if op == "tap":
                x, y = to_target(body.get("cx"), body.get("cy"))
                if vid:
                    bridge.tap(x, y, *vid)
                else:
                    worker.put("tap", x, y)
                self._json(200, {"ok": True, "x": x, "y": y, "via": "scrcpy" if vid else "adb"})
            elif op == "swipe":
                x1, y1 = to_target(body.get("cx1"), body.get("cy1"))
                x2, y2 = to_target(body.get("cx2"), body.get("cy2"))
                ms = max(50, min(3000, int(_num(body.get("ms"), 200))))
                if vid:
                    bridge.swipe(x1, y1, x2, y2, *vid, ms=ms)
                else:
                    worker.put("swipe", x1, y1, x2, y2, ms)
                self._json(200, {"ok": True, "via": "scrcpy" if vid else "adb"})
            elif op == "key":
                name, code = body.get("name"), body.get("code")
                named = {"back": 4, "home": 3, "recent": 187, "enter": 66}
                if name in named:
                    kc = named[name]
                elif isinstance(code, int) and not isinstance(code, bool) and code in KEYCODES:
                    kc = code                     # keyboard passthrough (allow-listed)
                else:
                    return self._json(400, {"error": "bad key"})
                if key_via_scrcpy:
                    try:
                        worker.put_wait("consolekey", bridge, kc)
                        return self._json(200, {"ok": True, "via": "scrcpy"})
                    except Exception as e:
                        return self._json(503, {"error": str(e)})
                try:
                    worker.put_wait("key", name if name in named else kc)
                except Exception as e:
                    return self._json(503, {"error": str(e)})
                self._json(200, {"ok": True, "via": "adb"})
            elif op == "text":
                text = body.get("text") or ""
                if not text:
                    return self._json(400, {"error": "empty text"})
                try:
                    worker.put_wait("screentext", text)
                except Exception as e:
                    return self._json(503, {"error": str(e)})
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})

    return Handler


def _peer_closed(sock) -> bool:
    """True if the client has closed its end (readable + EOF on a peek)."""
    try:
        r, _w, _x = select.select([sock], [], [], 0)
        return bool(r) and sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def _link_unique(part: str, directory: str, name: str) -> str:
    """Give the finished .part its final name without ever overwriting: hard-link
    to a free name (link fails if it exists → try the next -N), then drop .part."""
    taken = lambda n: os.path.lexists(os.path.join(directory, n))
    for _ in range(1000):
        final = unique_name(name, taken)
        try:
            os.link(part, os.path.join(directory, final))
        except FileExistsError:
            continue                      # lost a race; pick the next free name
        os.unlink(part)
        return final
    raise OSError("no free name for " + name)


def _num(v, default=0):
    try:
        n = float(v)
        return n if math.isfinite(n) else default
    except (TypeError, ValueError):
        return default


def serve(worker: Worker, host: str, port: int, tmpdir: str = None,
          screen: ScreenBuffer = None, screen_enabled: bool = True,
          fs_root: str = "~", max_upload: int = DEFAULT_MAX_UPLOAD,
          allow_hosts=(), disk_free=None, bridge=None, hub=None,
          profile=DEFAULT_PROFILE) -> ThreadingHTTPServer:
    """Build the server (worker already startable). Caller runs serve_forever."""
    state = State()
    state.bridge = bridge
    state.hub = hub
    if bridge is not None:
        bridge.on_change = state.bump
    owned_tmp = tempfile.TemporaryDirectory(prefix="android-console-") if tmpdir is None else None
    tmpdir = owned_tmp.name if owned_tmp else tmpdir
    if screen is None:
        screen = ScreenBuffer()

    class ConsoleServer(ThreadingHTTPServer):
        daemon_threads = True
        console_state = state

        def server_close(self):
            state.closed.set()
            super().server_close()
            if owned_tmp:
                owned_tmp.cleanup()

    try:
        handler = make_handler(worker, state, tmpdir, screen, screen_enabled,
                               LocalRoot(fs_root), max_upload=max_upload,
                               allow_hosts=tuple(allow_hosts), disk_free=disk_free, profile=profile)
        httpd = ConsoleServer((host, port), handler)
    except BaseException:
        if owned_tmp:
            owned_tmp.cleanup()
        raise
    threading.Thread(target=_pump, args=(worker, state), daemon=True).start()
    return httpd


def _is_local(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "")


def run(serial=None, addr=None, adb="adb", host="127.0.0.1", port=8801, allow_screen=False,
        fs_root="~", max_upload=DEFAULT_MAX_UPLOAD, allow_hosts=(), scrcpy_server=None,
        use_scrcpy=True, video_mode=None, video_max_fps=60, video_bit_rate=4_000_000,
        video_max_size=1280, vd_dpi="auto", vd_max_size=1280, profile=DEFAULT_PROFILE):
    dev = connect(serial, addr, adb=adb)
    screen = ScreenBuffer()
    screen_enabled = _is_local(host) or allow_screen
    worker = Worker(dev, screen=screen)
    bridge, hub, httpd = None, None, None
    # The temporary directory, worker, lease and bridge are owned by this run,
    # including startup failures (e.g. a port already in use).
    import signal
    def _term(*_):
        raise KeyboardInterrupt
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[sig] = signal.signal(sig, _term)
        except (ValueError, AttributeError):
            pass
    with tempfile.TemporaryDirectory(prefix="android-console-") as tmpdir:
        try:
            worker.start()
            bridge_note = "off"
            if screen_enabled and use_scrcpy:
                try:
                    bridge = ScrcpySession(dev, ensure_server(scrcpy_server, cache_dir=profile.cache_dir))
                    hub = VideoHub(bridge, dev, mode=video_mode or profile.default_mode,
                                   max_fps=video_max_fps, bit_rate=video_bit_rate,
                                   max_size=video_max_size, dpi_mode=vd_dpi, vd_max_size=vd_max_size,
                                   target_app=profile.target_app,
                                   system_decorations=profile.system_decorations)
                    bridge.start()
                    bridge_note = "scrcpy-server v%s (Apache-2.0)" % SCRCPY_VERSION
                except (ScrcpyError, OSError) as e:
                    bridge_note = "unavailable: %s" % e
            httpd = serve(worker, host, port, tmpdir=tmpdir, screen=screen,
                          screen_enabled=screen_enabled, fs_root=fs_root,
                          max_upload=max_upload, allow_hosts=allow_hosts, bridge=bridge,
                          hub=hub, profile=profile)
            shown = host if host not in ("0.0.0.0", "") else "<host-ip>"
            print(f"{profile.name} on http://{shown}:{port}  (Ctrl-C to stop)", flush=True)
            print(f"  screen: {'ON' if screen_enabled else 'OFF'}; device bridge: {bridge_note}", flush=True)
            if not _is_local(host):
                print("  No authentication: network clients can control the Android device. "
                      "Use a trusted network or loopback + SSH tunnel.", flush=True)
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if bridge is not None:
                bridge.stop()
            if hub is not None:
                hub.close()
            if httpd is not None:
                httpd.server_close()
                httpd.console_state.closed.set()
            worker.put("quit")
            worker.join(timeout=6)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


# --- the single page: device console in the TUI tone (monospace, dark, reverse-video bars) ---
_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#16161e">
<title>@@NAME@@ 콘솔</title>
<style>
  :root{
    --bg:#16161e; --fg:#c0caf5; --dim:#565f89; --bar:#2a2e42; --barfg:#c0caf5;
    --sel:#3d59a1; --selfg:#ffffff; --accent:#7aa2f7; --unread:#e0af68;
    --line:#2a2e42; --me:#9ece6a;
    --safe-b:env(safe-area-inset-bottom,0px); --safe-t:env(safe-area-inset-top,0px);
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--fg);
    font-family:"JetBrains Mono","DejaVu Sans Mono","D2Coding",Menlo,Consolas,monospace;
    font-size:14px;line-height:1.5;display:flex;flex-direction:column;
    height:100vh;height:100dvh;overflow:hidden}
  button{font:inherit;color:inherit;background:transparent;border:none;cursor:pointer}
  .bar{background:var(--bar);color:var(--barfg);padding:4px 10px;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;flex:0 0 auto}
  .bar .k{color:var(--accent)}
  #header{display:flex;align-items:center;gap:10px;padding-top:calc(4px + var(--safe-t))}
  #header .hint{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #header button{color:var(--accent);padding:0 8px}
  .statusbar{display:flex;align-items:center;gap:8px;padding-bottom:calc(4px + var(--safe-b))}
  .statusbar #status{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .badge{color:var(--accent);font-size:12px;white-space:nowrap}
  .console{flex:1 1 auto;display:flex;min-height:0;overflow:hidden}
  .side{flex:0 0 34%;max-width:460px;min-width:260px;display:flex;flex-direction:column;
    border-right:1px solid var(--line);min-height:0}
  .fpane{flex:1 1 50%;display:flex;flex-direction:column;min-height:0;border-bottom:1px solid var(--line)}
  .fhead{display:flex;align-items:center;gap:6px;padding:4px 8px;background:var(--bar)}
  .fhead b{color:var(--accent);white-space:nowrap;font-size:13px}
  .fhead input{flex:1;min-width:0;background:transparent;border:1px solid var(--line);
    border-radius:3px;color:var(--fg);font-family:inherit;font-size:13px;padding:2px 6px;outline:none}
  .fhead .fbtn{color:var(--accent);border:1px solid var(--line);border-radius:3px;padding:1px 8px;
    white-space:nowrap;font-size:12px}
  .flist{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;font-size:13px}
  .frow{display:flex;gap:8px;padding:3px 10px;cursor:pointer;white-space:nowrap}
  .frow:hover{background:var(--line)}
  .frow .fn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
  .frow .fs{color:var(--dim)}
  .frow.dir .fn{color:var(--accent)}
  .frow.sel{background:var(--sel);color:var(--selfg)}
  .frow.sel .fn,.frow.sel .fs{color:var(--selfg)}
  .frow .fdl{color:var(--accent);padding:0 4px;visibility:hidden}
  .frow:hover .fdl,.frow.sel .fdl{visibility:visible}
  @media (hover:none){ .frow .fdl{visibility:visible} }
  .fpane.drop{outline:2px dashed var(--accent);outline-offset:-3px}
  .ffoot{display:flex;gap:6px;padding:4px 8px;border-top:1px solid var(--line);align-items:center}
  .ffoot .fsel{flex:1;min-width:0;color:var(--dim);font-size:12px;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  .ffoot button{color:var(--accent);border:1px solid var(--line);border-radius:3px;padding:3px 10px;white-space:nowrap}
  .ffoot button:disabled{color:var(--dim);cursor:default}
  .ferr{color:#f7768e;padding:6px 10px;font-size:12px;white-space:normal}
  .empty{color:var(--dim);padding:8px 12px}
  .jobs{flex:0 0 auto;max-height:22%;overflow-y:auto;font-size:12px;padding:2px 8px}
  .files-dialog{padding:0;color:var(--fg);background:var(--bg);border:1px solid var(--accent);
    border-radius:6px;width:min(720px,calc(100vw - 24px));height:min(760px,calc(100vh - 24px));
    height:min(760px,calc(100dvh - 24px));max-width:none;max-height:none;overflow:hidden;
    box-shadow:0 8px 36px #0008}
  .files-dialog[open]{display:flex;flex-direction:column}
  .files-dialog::backdrop{background:rgba(0,0,0,.65)}
  .files-bar{display:flex;align-items:center;gap:12px;padding:6px 10px;background:var(--bar);flex:0 0 auto}
  .files-bar h2{font:inherit;color:var(--accent);margin:0;flex:1}
  .files-bar button{min-height:36px;padding:4px 8px;color:var(--accent)}
  .files-dialog .side{flex:1 1 auto;max-width:none;min-width:0;border-right:none;overflow:hidden}
  .job{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .job.running{color:var(--unread)} .job.done{color:var(--me)} .job.error{color:#f7768e}
  .center{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;min-height:0;padding:6px}
  .controls{display:flex;gap:6px;align-items:center;justify-content:flex-end;flex-wrap:wrap;border-top:1px solid var(--line)}
  .controls button{color:var(--accent);border:1px solid var(--line);border-radius:4px;width:clamp(90px,28vw,128px);padding:6px 12px;min-height:36px;
    white-space:nowrap}
  .controls button:hover{background:var(--line)}
  #header .fps{color:var(--dim);font-size:12px;white-space:nowrap;flex:0 0 auto}
  .scrwrap{flex:1 1 auto;min-height:0;display:flex;align-items:center;justify-content:center;
    background:#000;border:1px solid var(--line);border-radius:4px;position:relative;touch-action:none}
  .scrwrap.kbon{box-shadow:0 0 0 2px var(--accent) inset}
  #scr,#vcanvas,#vvideo{max-width:100%;max-height:100%;object-fit:contain;touch-action:none;
    user-select:none;-webkit-user-select:none}
  #scr[hidden],#vcanvas[hidden],#vvideo[hidden]{display:none}
  .statusbar select{background:var(--bar);color:var(--accent);border:1px solid var(--line);
    border-radius:3px;font:inherit;font-size:12px;padding:0 2px}
  #kbcap{position:absolute;left:0;top:0;width:1px;height:1px;opacity:0;border:0;padding:0;
    resize:none;font-size:16px}
  .toast{position:fixed;right:12px;bottom:calc(40px + var(--safe-b));max-width:min(460px,92vw);
    background:var(--bar);border:1px solid var(--accent);border-radius:6px;padding:8px 10px;
    z-index:15;display:flex;gap:8px;align-items:center;box-shadow:0 4px 18px rgba(0,0,0,.5)}
  .toast[hidden]{display:none}
  .toast .tl{color:var(--dim);white-space:nowrap}
  .toast .tp{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .toast button{color:var(--accent);border:1px solid var(--line);border-radius:3px;padding:2px 8px}
  .statusbar label{font-size:12px;color:var(--dim);white-space:nowrap;cursor:pointer}
  .disabled-note{color:var(--unread);padding:16px;text-align:center;white-space:normal}
  .ovl{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;z-index:20;padding-top:10vh}
  .ovl.show{display:block}
  .panel{max-width:560px;margin:0 auto;background:var(--bg);border:1px solid var(--line);
    border-radius:6px;overflow:hidden;box-shadow:0 6px 30px rgba(0,0,0,.5)}
  .panel h3{margin:0;padding:10px 12px;color:var(--accent);border-bottom:1px solid var(--line)}
  .panel .help{padding:12px 16px;line-height:1.8;white-space:pre-wrap}
  .panel .help b{color:var(--accent)}
  @media (max-width:720px){
    #header .hint .long{display:none}
    .console{flex-direction:column}
    .controls button{min-height:40px}
    .statusbar{flex-wrap:wrap;gap:4px}
    .statusbar #status{flex:1 0 100%}
    .statusbar #vmode{max-width:100%;overflow:hidden;text-overflow:ellipsis}
  }
</style></head>
<body>
  <div class="bar" id="header"><span class="hint"><span class="k">@@NAME@@</span> 콘솔<span class="long"> —
    화면 클릭: 탭·키보드 · 드래그·휠: 스크롤 · Esc: 키보드 해제</span></span>
    <span class="fps" id="cfps" title="영상 프레임률"></span>
    <button id="files-open" aria-haspopup="dialog" aria-controls="files-dialog" aria-expanded="false">파일 탐색</button>
    <button id="helpbtn" title="도움말 (?)">?</button></div>
  <div class="console" id="console">
    <div class="center">
      <div class="scrwrap" id="conwrap"><textarea id="kbcap" autocomplete="off" spellcheck="false"
        aria-label="기기 키보드 입력"></textarea><img id="scr" alt="기기 화면" draggable="false"><canvas
        id="vcanvas" hidden aria-label="기기 화면(H.264)"></canvas><video id="vvideo" hidden muted
        playsinline autoplay disablepictureinpicture aria-label="기기 화면(H.264)"></video></div>
    </div>
  </div>
  <dialog id="files-dialog" class="files-dialog" aria-labelledby="files-title">
    <div class="files-bar"><h2 id="files-title">파일 탐색</h2>
      <button id="files-close" autofocus aria-label="파일 탐색 닫기">닫기 ✕</button></div>
    <div class="side">
      <div class="fpane" id="fp-android">
        <div class="fhead"><b>안드로이드</b>
          <input id="fpath-android" spellcheck="false" autocomplete="off" aria-label="안드로이드 경로">
          <button class="fbtn" id="up-android" title="브라우저 파일을 이 기기 폴더로 (드래그앤드롭도 됨)">⇪ 올리기</button>
          <input type="file" id="upf-android" multiple hidden></div>
        <div class="flist" id="flist-android" role="listbox" aria-label="안드로이드 파일"></div>
        <div class="ffoot"><span class="fsel" id="fsel-android">파일 선택</span>
          <button id="pull" disabled title="선택 파일을 아래 로컬 폴더로 복사">↓ 로컬로 복사</button></div>
      </div>
      <div class="fpane" id="fp-local">
        <div class="fhead"><b>로컬(VM)</b>
          <input id="fpath-local" spellcheck="false" autocomplete="off" aria-label="로컬 경로" placeholder="(루트)">
          <button class="fbtn" id="up-local" title="브라우저 파일을 이 VM 폴더로 (드래그앤드롭도 됨)">⇪ 올리기</button>
          <input type="file" id="upf-local" multiple hidden></div>
        <div class="flist" id="flist-local" role="listbox" aria-label="로컬 파일"></div>
        <div class="ffoot"><span class="fsel" id="fsel-local">파일 선택</span>
          <button id="push" disabled title="선택 파일을 위 안드로이드 폴더로 복사">↑ 안드로이드로 복사</button></div>
      </div>
      <div class="jobs" id="jobs" aria-live="polite"></div>
    </div>
  </dialog>
  <div class="bar controls" id="controls" role="group" aria-label="기기 조작">
    <button id="ckback">Back</button>@@NAVIGATION@@<button id="ckenter">Enter</button>
    <button id="ckclip" title="브라우저 클립보드를 기기로 붙여넣기 (화면 포커스 중 Ctrl/Cmd+V도 됨)">📋 기기로</button>
  </div>
  <div class="bar statusbar" id="statusbar">
    <span id="status" role="status">starting …</span>
    <label id="clipauto-l" hidden title="기기에서 복사하면 브라우저 클립보드에 자동으로 (https/localhost에서만)">
      <input type="checkbox" id="clipauto"> 자동 복사</label>
    <span class="badge" id="clipbadge" title="기기 클립보드 연동(scrcpy)">📋 —</span>
    <select id="dsel" title="디스플레이: 가상(브라우저 크기에 맞춤) / 미러(기기 화면)">
      @@MODES@@</select>
    <select id="rsel" title="화면 전달: 자동 / WebCodecs / MSE / PNG">
      <option value="auto">자동</option><option value="wc">WC</option><option value="mse">MSE</option>
      <option value="png">PNG</option></select>
    <select id="qsel" title="가상 화면 긴 변: 낮추면 더 부드럽게 표시됩니다. 연결된 화면 모두에 적용됩니다.">
      <option value="960">빠르게 960</option><option value="1280" selected>균형 1280</option>
      <option value="1440">선명하게 1440</option><option value="1920">최대 1920</option></select>
    <span class="badge" id="vmode" title="화면 전달 방식">PNG</span>
  </div>
  <div class="toast" id="cliptoast" hidden role="status">
    <span class="tl">기기 클립보드:</span><span class="tp" id="clipprev"></span>
    <button id="clipcopy">복사</button><button id="clipx" aria-label="닫기">✕</button>
  </div>
  <div class="ovl" id="helpovl"><div class="panel"><h3>콘솔 도움말</h3><div class="help" id="helptext"></div></div></div>
<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = s => (s||"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const lsGet = (k,d) => { try{ return JSON.parse(localStorage.getItem(k)) ?? d; }catch(e){ return d; } };
const lsSet = (k,v) => { try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} };
const HELP =
  '<b>화면 클릭</b>  기기 탭 + 키보드가 기기로 (테두리 표시)\n' +
  '<b>드래그</b>  실시간 스크롤 (PNG는 놓은 뒤 전달)\n' +
  '<b>휠 / 트랙패드</b>  화면 위에서 위아래 스크롤 (영상 모드)\n' +
  '<b>키보드</b>  글자는 ADBKeyBoard(한글 OK), Backspace·Enter·Tab·방향키·Home/End·PgUp/PgDn·Delete\n' +
  '<b>Esc</b>  키보드 해제 · <b>Back/Enter</b> 버튼\n' +
  '<b>파일 탐색</b>  파일 창 열기 · Esc / 닫기 버튼으로 닫기\n' +
  '<b>⇪ 올리기 / 드래그앤드롭</b>  브라우저 파일을 그 패널 폴더로\n' +
  '<b>⤓ / 더블클릭</b>  파일을 브라우저로 받기\n' +
  '<b>↓ 로컬로 / ↑ 안드로이드로</b>  기기 ⇄ VM 복사(덮어쓰지 않음)\n' +
  '<b>?</b>  이 도움말@@HELP_SUFFIX@@';
$("helptext").innerHTML = HELP;
function openHelp(){ $("helpovl").classList.add("show"); }
function closeHelp(){ $("helpovl").classList.remove("show"); }
$("helpbtn").onclick = openHelp;
$("helpovl").addEventListener("click", e=>{ if(e.target.id==="helpovl") closeHelp(); });
document.addEventListener("keydown", e=>{
  if($("files-dialog").open) return;   // native dialog owns focus and Escape
  const t = e.target;
  if(t.tagName==="INPUT" || t.tagName==="TEXTAREA") return;   // path boxes / kbcap
  if(e.key==="Escape"){ closeHelp(); return; }
  if(e.key==="?"){ e.preventDefault(); openHelp(); }
});
let tempStatus = 0;
function setStatus(s){ $("status").textContent = " "+s; tempStatus = Date.now(); }

// ---- server status line (worker errors etc.) ----
let stSeq = -1, clipSeq = null, clipText = "";
async function pollState(){
  try{
    const r = await fetch("/api/state?since="+stSeq);
    if(r.status===200){ const j = await r.json(); stSeq = j.seq;
      if(Date.now()-tempStatus > 4000) $("status").textContent = " "+(j.status||"");
      $("clipbadge").textContent = j.scrcpy ? "📋 연동" : "📋 —";
      if(clipSeq===null) clipSeq = j.clip_seq;            // don't toast what was there before
      else if(j.clip_seq !== clipSeq){ clipSeq = j.clip_seq; deviceClipChanged(); }
    }
  }catch(e){}
}
// ---- clipboard: device → browser (toast / opt-in auto copy), browser → device ----
const secure = window.isSecureContext && !!(navigator.clipboard && navigator.clipboard.writeText);
if(secure){ $("clipauto-l").hidden = false; $("clipauto").checked = lsGet("@@STORAGE@@.clipauto", false); }
$("clipauto").onchange = ()=> lsSet("@@STORAGE@@.clipauto", $("clipauto").checked);
async function deviceClipChanged(){
  let j; try{ j = await (await fetch("/api/clip")).json(); }catch(e){ return; }
  clipText = j.text || "";
  if(!clipText) return;
  if(secure && $("clipauto").checked){
    try{ await navigator.clipboard.writeText(clipText); setStatus("기기 클립보드를 브라우저로 복사함"); return; }
    catch(e){ /* no focus/permission → fall back to the toast */ }
  }
  $("clipprev").textContent = clipText.replace(/\s+/g," ").slice(0,120);
  $("cliptoast").hidden = false;
}
function copyText(t){
  if(secure){ return navigator.clipboard.writeText(t).then(()=>true, ()=>legacyCopy(t)); }
  return Promise.resolve(legacyCopy(t));
}
function legacyCopy(t){                         // http fallback; needs the click gesture
  const ta = document.createElement("textarea"); ta.value = t;
  ta.style.position="fixed"; ta.style.opacity="0"; document.body.appendChild(ta);
  ta.select(); let ok=false; try{ ok = document.execCommand("copy"); }catch(e){}
  ta.remove(); return ok;
}
$("clipcopy").onclick = async ()=>{
  const ok = await copyText(clipText);
  setStatus(ok ? "브라우저 클립보드로 복사함" : "복사 실패 — 미리보기를 직접 선택해 복사하세요");
  if(ok) $("cliptoast").hidden = true;
};
$("clipx").onclick = ()=>{ $("cliptoast").hidden = true; };
async function clipToDevice(text, paste){
  try{
    const r = await fetch("/api/clip", {method:"POST", headers:{"Content-Type":"application/json"},
                                        body: JSON.stringify({text, paste})});
    const j = await r.json();
    setStatus(r.ok ? (paste ? "기기에 붙여넣음" : "기기 클립보드에 넣음") +
              (j.via==="adbkeyboard" ? " (타이핑)" : "") : ("클립보드 실패: "+(j.error||r.status)));
  }catch(e){ setStatus("클립보드 실패: 연결"); }
}
$("ckclip").onclick = async ()=>{
  if(navigator.clipboard && navigator.clipboard.readText && window.isSecureContext){
    try{ const t = await navigator.clipboard.readText(); if(t){ clipToDevice(t, true); return; } }
    catch(e){ /* permission denied → hint below */ }
  }
  $("kbcap").focus({preventScroll:true});
  setStatus("화면을 클릭한 뒤 Ctrl/Cmd+V 로 붙여넣으세요 (http 접속에서는 버튼으로 읽을 수 없음)");
};

// ---- live screen: PNG frames (fallback renderer) ----
let frameLoop=false, fpsCount=0, fpsT=Date.now(), scrSeq=0, pngWanted=true;
function startFrames(){ if(!frameLoop){ frameLoop=true; scrSeq=0; pollFrame(); } }
// poll the tiny /info (keeps the viewer alive) and fetch a frame only when it changed
function pollFrame(){
  if(!pngWanted){ frameLoop=false; return; }
  fetch("/api/screen/info").then(r=>r.json()).then(j=>{
    if(!pngWanted){ frameLoop=false; return; }
    if(j.seq && j.seq !== scrSeq){
      scrSeq = j.seq;
      const nx = new Image();
      nx.onload = ()=>{
        if(!pngWanted) return;
        $("scr").src = nx.src;
        fpsCount++; const now=Date.now();
        if(now-fpsT >= 1000){ $("cfps").textContent = fpsCount+" fps"; fpsCount=0; fpsT=now; badge(); }
      };
      nx.src = "/api/screen/frame?seq=" + j.seq;
    }
    setTimeout(pollFrame, 250);
  }).catch(()=>{ if(pngWanted) setTimeout(pollFrame, 600); else frameLoop=false; });
}
async function scrPost(path, body){
  body.src ??= renderer==="png" ? "png" : "video";
  try{ const r = await fetch("/api/screen/"+path, {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify(body),
    signal:AbortSignal.timeout(5000), keepalive:path==="touch" && ["up","cancel"].includes(body.action)});
    if(!r.ok) setStatus("입력 실패: " + r.status);
    return r.ok;
  }catch(e){ setStatus("입력 연결 실패"); return false; }
}
// Live input: ordered requests, one in flight and one pending move/wheel.
// A slow link coalesces movement instead of replaying stale pointer positions.
let pd = null, touchQ = [], touchBusy = false, moveT = null, holdT = null;
async function touchPump(){
  if(touchBusy) return;
  touchBusy = true;
  try{ while(touchQ.length){
    const e = touchQ.shift();
    if(!await scrPost(e.path, e.body)){
      touchQ = touchQ.filter(n=>n.body.gesture !== e.body.gesture);
      if(pd && pd.gesture===e.body.gesture) cancelPointer();
    }
  }}finally{ touchBusy = false; }
}
function touchEnqueue(path, body){
  const tail = touchQ[touchQ.length-1];
  if(path==="touch" && body.action==="move" && tail?.path===path &&
     tail.body.action==="move" && tail.body.gesture===body.gesture) tail.body=body;
  else if(path==="scroll" && tail?.path===path){
    body.dy = Math.max(-16, Math.min(16, body.dy+tail.body.dy)); tail.body=body;
  }else touchQ.push({path,body});
  touchPump();
}
function dims(el){
  if(el.tagName==="IMG") return [el.naturalWidth, el.naturalHeight];
  if(el.tagName==="CANVAS") return [el.width, el.height];
  return [el.videoWidth, el.videoHeight];
}
function point(el, e){
  const r=el.getBoundingClientRect(), [iw,ih]=dims(el);
  return {cx:e.clientX-r.left, cy:e.clientY-r.top, ew:r.width, eh:r.height, iw, ih};
}
function pointerSend(action){
  if(pd?.live) touchEnqueue("touch", {...pd.pos, src:"video", gesture:pd.gesture, action});
}
function cancelPointer(){
  clearTimeout(moveT); clearInterval(holdT); moveT=holdT=null;
  pointerSend("cancel"); pd=null;
}
["scr","vcanvas","vvideo"].forEach(id=>{
  const el = $(id);
  el.addEventListener("pointerdown", e=>{
    if(e.button!==0 || pd) return;
    e.preventDefault();
    const pos=point(el,e); if(!pos.iw || !pos.ih) return;
    pd={pos, x:pos.cx, y:pos.cy, t:Date.now(), id:e.pointerId, el,
        live:renderer!=="png", gesture:Date.now()+":"+Math.random().toString(36).slice(2)};
    pointerSend("down");
    if(pd.live) holdT=setInterval(()=>pointerSend("move"),500);
    try{ el.setPointerCapture(e.pointerId); }catch(_){}
    $("kbcap").focus({preventScroll:true}); });
  el.addEventListener("pointermove", e=>{
    if(!pd || pd.id!==e.pointerId || pd.el!==el) return;
    pd.pos=point(el,e);
    if(pd.live && !moveT) moveT=setTimeout(()=>{ moveT=null; pointerSend("move"); },16);
  });
  el.addEventListener("pointerup", e=>{
    if(!pd || pd.id!==e.pointerId || pd.el!==el) return;
    clearTimeout(moveT); clearInterval(holdT); moveT=holdT=null;
    pd.pos=point(el,e);
    if(pd.live) pointerSend("up");
    else if(Math.hypot(pd.pos.cx-pd.x,pd.pos.cy-pd.y)>12)
      scrPost("swipe",{...pd.pos, src:"png", cx1:pd.x,cy1:pd.y,cx2:pd.pos.cx,cy2:pd.pos.cy,ms:Math.max(120,Date.now()-pd.t)});
    else scrPost("tap",{...pd.pos,src:"png",cx:pd.x,cy:pd.y});
    pd=null;
  });
  for(const name of ["pointercancel","lostpointercapture"])
    el.addEventListener(name,e=>{ if(pd?.id===e.pointerId) cancelPointer(); });
  el.addEventListener("wheel", e=>{
    if(e.ctrlKey || e.metaKey || renderer==="png" || pd) return;
    e.preventDefault();
    if(e.shiftKey || !e.deltaY || Math.abs(e.deltaX)>Math.abs(e.deltaY)) return;
    const pos=point(el,e); if(!pos.iw || !pos.ih) return;
    const scale=e.deltaMode===1 ? 16 : e.deltaMode===2 ? pos.eh : 1;
    touchEnqueue("scroll",{...pos,src:"video",dx:0,
      dy:Math.max(-16,Math.min(16,-e.deltaY*scale/120))});
  },{passive:false});
});
window.addEventListener("blur",cancelPointer);
window.addEventListener("pagehide",cancelPointer);
document.addEventListener("visibilitychange",()=>{ if(document.hidden) cancelPointer(); });
for(const [id,name] of [["ckhome","home"],["ckrecent","recent"]]){
  if($(id)) $(id).onclick = ()=>{ kbFlush(); keyPost("key",{name}); };
}
$("ckback").onclick = ()=>{ kbFlush(); keyPost("key",{name:"back"}); };
$("ckenter").onclick = ()=>{ kbFlush(); keyPost("key",{name:"enter"}); };

const filesDialog = $("files-dialog");
$("files-open").onclick = ()=>{
  cancelPointer(); $("kbcap").blur(); closeHelp();
  if(!filesDialog.open) filesDialog.showModal();
  $("files-open").setAttribute("aria-expanded","true");
};
$("files-close").onclick = ()=>filesDialog.close();
filesDialog.addEventListener("close",()=>$("files-open").setAttribute("aria-expanded",String(filesDialog.open)));
filesDialog.addEventListener("click",e=>{
  const r=filesDialog.getBoundingClientRect();
  if(e.target===filesDialog && (e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom))
    filesDialog.close();
});

// ---- file managers (top: android, bottom: local VM disk) — copy only ----
const fp = {
  android: {path:"/sdcard/", parent:null, items:[], sel:null, loaded:false},
  local:   {path:"",         parent:null, items:[], sel:null, loaded:false},
};
function hsize(n){
  if(n<1024) return n+"B";
  if(n<1048576) return (n/1024).toFixed(1)+"K";
  if(n<1073741824) return (n/1048576).toFixed(1)+"M";
  return (n/1073741824).toFixed(1)+"G";
}
async function loadPane(which, path){
  let r, j;
  try{
    r = await fetch("/api/fs/"+which+"?path="+encodeURIComponent(path||""));
    j = await r.json();
  }catch(e){ renderPane(which, "연결 실패"); return; }
  if(!r.ok){ renderPane(which, j.error || ("HTTP "+r.status)); return; }
  Object.assign(fp[which], {path:j.path, parent:j.parent, items:j.items, sel:null, loaded:true});
  if(which==="local") $("fpath-local").title = j.root + (j.path ? "/"+j.path : "");
  renderPane(which, j.error);
}
function navigable(which, it){ return it.type==="dir" || (which==="android" && it.type==="link"); }
function renderPane(which, err){
  const P = fp[which], list = $("flist-"+which);
  $("fpath-"+which).value = P.path || "";
  list.innerHTML = "";
  if(err){ const d=document.createElement("div"); d.className="ferr"; d.textContent=err; list.appendChild(d); }
  const atTop = which==="android" ? (P.path==="/" || P.parent==null) : (P.parent==null);
  if(!atTop){
    const up=document.createElement("div"); up.className="frow dir"; up.setAttribute("role","option");
    up.innerHTML='<span class="fn">▴ ..</span><span class="fs"></span>';
    up.onclick=()=> loadPane(which, P.parent); list.appendChild(up);
  }
  for(const it of P.items){
    const d=document.createElement("div");
    const nav = navigable(which, it);
    d.className = "frow " + (nav ? "dir" : "file");
    d.setAttribute("role","option"); d.dataset.path = it.path;
    d.innerHTML = '<span class="fn">'+(nav ? (it.type==="link"?"↪ ":"▸ ") : "· ")+esc(it.name)+
      '</span><span class="fs">'+(nav ? "" : hsize(it.size))+'</span>'+
      (nav ? '' : '<button class="fdl" title="브라우저로 받기">⤓</button>');
    d.onclick = ()=>{
      if(nav) loadPane(which, which==="android" ? it.path+"/" : it.path);
      else { P.sel = it; renderSel(which); }
    };
    if(!nav){
      d.querySelector(".fdl").onclick = (e)=>{ e.stopPropagation(); download(which, it); };
      d.ondblclick = ()=> download(which, it);
    }
    list.appendChild(d);
  }
  if(!P.items.length && !err){ const e=document.createElement("div"); e.className="empty"; e.textContent="(비어 있음)"; list.appendChild(e); }
  renderSel(which);
}
function renderSel(which){
  const P = fp[which];
  $("flist-"+which).querySelectorAll(".frow").forEach(r=>{
    const on = !!P.sel && r.dataset.path===P.sel.path;
    r.classList.toggle("sel", on); r.setAttribute("aria-selected", on);
  });
  $("fsel-"+which).textContent = P.sel ? P.sel.name+" ("+hsize(P.sel.size)+")" : "파일 선택";
  $(which==="android" ? "pull" : "push").disabled = !P.sel;
}
["android","local"].forEach(w=>{
  $("fpath-"+w).addEventListener("keydown", e=>{
    if(e.key==="Enter" && !e.isComposing){ e.preventDefault(); loadPane(w, $("fpath-"+w).value.trim()); }
  });
});

// ---- copy jobs (background on the server; polled here) ----
const jobs = [];
function renderJobs(){
  $("jobs").innerHTML = jobs.slice(-6).map(j=>
    '<div class="job '+j.state+'">'+({running:"⋯",done:"✓",error:"✗"}[j.state]||"")+" "+
    esc(j.label)+(j.note ? " — "+esc(j.note) : "")+'</div>').join("");
}
async function watchJob(id, job, reloadWhich, reloadPath){
  for(let i=0;i<600;i++){                       // up to ~7 min for big files
    await new Promise(res=>setTimeout(res, 700));
    let s; try{ s = await (await fetch("/api/fs/job/"+id)).json(); }catch(e){ continue; }
    if(s.state==="done"){ job.state="done"; job.note="→ "+s.dst; renderJobs();
      loadPane(reloadWhich, reloadPath); return; }
    if(s.state==="error"){ job.state="error"; job.note=s.error; renderJobs(); return; }
  }
  job.state="error"; job.note="시간 초과"; renderJobs();
}
async function transfer(op){
  const src = op==="pull" ? fp.android.sel : fp.local.sel;
  if(!src) return;
  const dst = op==="pull" ? fp.local.path : fp.android.path;
  const job = {state:"running", label:(op==="pull" ? "↓ " : "↑ ")+src.name, note:""};
  jobs.push(job); renderJobs();
  let r, j;
  try{
    r = await fetch("/api/fs/"+op, {method:"POST", headers:{"Content-Type":"application/json"},
                                    body:JSON.stringify({src:src.path, dst})});
    j = await r.json();
  }catch(e){ job.state="error"; job.note="연결 실패"; renderJobs(); return; }
  if(!r.ok){ job.state="error"; job.note=j.error||("HTTP "+r.status); renderJobs(); return; }
  await watchJob(j.id, job, op==="pull" ? "local" : "android", dst);
}
// ---- browser ⇄ VM/device: uploads (XHR for progress) and downloads ----
function uploadOne(which, f, dirOverride){
  return new Promise(resolve=>{
    const P = fp[which], dir = dirOverride || P.path || "";
    const job = {state:"running", label:"⇪ "+f.name+(which==="android"?" → 기기":" → VM"), note:"0%"};
    jobs.push(job); renderJobs();
    const q = (which==="android" ? "target=android&" : "") + "dir="+encodeURIComponent(dir) +
              "&name="+encodeURIComponent(f.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/fs/upload?"+q);
    xhr.setRequestHeader("@@UPLOAD_HEADER@@", "upload");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = e=>{ if(e.lengthComputable){ job.note=Math.floor(e.loaded*100/e.total)+"%"; renderJobs(); } };
    xhr.onload = async ()=>{
      let j = {}; try{ j = JSON.parse(xhr.responseText); }catch(_){}
      if(xhr.status===201){ job.state="done"; job.note="→ "+j.path; renderJobs(); loadPane("local", dir); }
      else if(xhr.status===202){ job.note="기기로 복사 중…"; renderJobs(); await watchJob(j.id, job, "android", dir); }
      else { job.state="error"; job.note = j.error || ("HTTP "+xhr.status); renderJobs(); }
      resolve();
    };
    xhr.onerror = ()=>{ job.state="error"; job.note="연결 실패"; renderJobs(); resolve(); };
    xhr.send(f);
  });
}
async function uploadFiles(which, files){ for(const f of files) await uploadOne(which, f); }
function download(which, it){
  const a = document.createElement("a");
  a.href = "/api/fs/"+which+"/get?path="+encodeURIComponent(it.path);
  a.download = it.name; document.body.appendChild(a); a.click(); a.remove();
}
["android","local"].forEach(w=>{
  $("up-"+w).onclick = ()=> $("upf-"+w).click();
  $("upf-"+w).addEventListener("change", e=>{ const fs=[...e.target.files]; e.target.value=""; uploadFiles(w, fs); });
  const pane = $("fp-"+w);
  ["dragenter","dragover"].forEach(ev=> pane.addEventListener(ev, e=>{ e.preventDefault(); pane.classList.add("drop"); }));
  ["dragleave","drop"].forEach(ev=> pane.addEventListener(ev, e=>{ e.preventDefault(); pane.classList.remove("drop"); }));
  pane.addEventListener("drop", e=>{ const fs=[...(e.dataTransfer?e.dataTransfer.files:[])]; if(fs.length) uploadFiles(w, fs); });
});
$("pull").onclick = ()=> transfer("pull");
$("push").onclick = ()=> transfer("push");

// ---- keyboard → device: a hidden textarea captures keys (IME-aware) ----
const kbcap = $("kbcap");
let kbuf = "", kbT = null;
let keyTail = Promise.resolve();
function keyPost(path,body){
  keyTail=keyTail.then(()=>scrPost(path,body)); return keyTail;
}
const KMAP = {Backspace:67, Enter:66, Tab:61, ArrowUp:19, ArrowDown:20, ArrowLeft:21,
              ArrowRight:22, Delete:112, Home:122, End:123, PageUp:92, PageDown:93};
function kbFlush(){
  if(kbT){ clearTimeout(kbT); kbT=null; }
  if(kbuf){ const t=kbuf; kbuf=""; keyPost("text", {text:t}); }
  return keyTail;
}
function kbQueue(t){                 // bounded batching: continuous typing also flushes every 60ms
  if(!t) return;
  kbuf += t;
  if(!kbT) kbT = setTimeout(kbFlush, 60);
}
kbcap.addEventListener("keydown", e=>{
  e.stopPropagation();
  if(e.isComposing || e.keyCode===229) return;           // Korean syllable still composing
  if(e.key==="Escape"){ e.preventDefault(); kbcap.blur(); return; }
  if(e.ctrlKey || e.metaKey || e.altKey) return;
  const code = KMAP[e.key];
  if(code!==undefined){ e.preventDefault(); kbFlush(); keyPost("key", {code}); }
});
kbcap.addEventListener("input", e=>{
  if(e.isComposing) return;                              // wait for compositionend
  const v = kbcap.value; kbcap.value = ""; kbQueue(v);
});
kbcap.addEventListener("compositionend", ()=>{
  const v = kbcap.value; kbcap.value = ""; kbQueue(v);   // committed syllables only
});
// Ctrl/Cmd+V while the screen has the keyboard: text → device clipboard + paste
// into the focused field (exact, keeps newlines); images/files → device Download
kbcap.addEventListener("paste", e=>{
  const cd = e.clipboardData; if(!cd) return;
  const files = [...(cd.files || [])];
  if(files.length){
    e.preventDefault();
    files.forEach((f,i)=>{ const named = f.name && f.name!=="image.png" ? f : new File([f],
      "paste-"+Date.now()+"-"+i+"."+((f.type||"").split("/")[1]||"bin"), {type:f.type});
      uploadOne("android", named, "/sdcard/Download/"); });
    return;
  }
  const t = cd.getData("text");
  if(t){ e.preventDefault(); kbFlush(); keyTail=keyTail.then(()=>clipToDevice(t, true)); }
});
kbcap.addEventListener("focus", ()=> $("conwrap").classList.add("kbon"));
kbcap.addEventListener("blur", ()=>{ kbFlush(); $("conwrap").classList.remove("kbon"); });


// ================= H.264 video: WebCodecs → MSE(fMP4) → PNG =================
const ORDER = ["wc","mse","png"];
let renderer = "png", vAbort = null, vFails = 0, vInfo = "", vfps = 0, vT = Date.now();
const canWC  = ()=> !!(window.VideoDecoder && window.isSecureContext);
const canMSE = ()=> !!(window.MediaSource && MediaSource.isTypeSupported('video/mp4; codecs="avc1.42C029"'));
const usable = r => r==="png" || (r==="wc" ? canWC() : canMSE());
function wanted(){
  const q = new URLSearchParams(location.search).get("video") || lsGet("@@STORAGE@@.video", "auto");
  if(ORDER.includes(q) && usable(q)) return q;
  return ORDER.find(usable);
}
function showRenderer(r){
  $("scr").hidden = r!=="png"; $("vcanvas").hidden = r!=="wc"; $("vvideo").hidden = r!=="mse";
}
function badge(){
  $("vmode").textContent = renderer==="png" ? ("PNG " + ($("cfps").textContent||"")) :
    ("H.264 " + renderer.toUpperCase() + (vInfo ? " " + vInfo : ""));
}
function vframe(){ vfps++; const now=Date.now(); if(now-vT>=1000){ $("cfps").textContent=vfps+" fps"; vfps=0; vT=now; badge(); } }
function stopVideo(){ cancelPointer(); if(vAbort){ try{ vAbort.abort(); }catch(_){} vAbort=null; } }
function usePNG(){ stopVideo(); renderer="png"; showRenderer("png"); pngWanted=true; startFrames(); badge(); }
function fallback(from, why){
  const next = ORDER.slice(ORDER.indexOf(from)+1).find(usable) || "png";
  setStatus("화면 전달 " + from.toUpperCase() + " 실패(" + why + ") → " + next.toUpperCase());
  startRenderer(next);
}
function startRenderer(r){
  stopVideo();
  if(r==="png"){ usePNG(); return; }
  pngWanted = false; renderer = r; showRenderer(r); badge();
  runStream(r).catch(e=>{
    if(e && e.name==="AbortError") return;
    if(++vFails <= 2 && renderer===r){ setTimeout(()=>{ if(renderer===r) startRenderer(r); }, 1200); return; }
    vFails = 0; fallback(r, (e && e.message) || "error");
  });
}
async function runStream(r){
  const box = $("conwrap").getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const controller = new AbortController(); vAbort=controller;
  const resp = await fetch("/api/video/stream?fmt=" + (r==="mse" ? "fmp4" : "annexb") +
    "&w=" + Math.round(box.width) + "&h=" + Math.round(box.height) + "&dpr=" + dpr, {signal: controller.signal});
  if(!resp.ok) throw new Error("HTTP " + resp.status);
  const sink = r==="wc" ? wcSink() : mseSink();
  const reader = resp.body.getReader();
  let buf = new Uint8Array(0);
  try{ for(;;){
    const {value, done} = await reader.read();
    if(done) throw new Error("stream ended");
    const nb = new Uint8Array(buf.length + value.length); nb.set(buf); nb.set(value, buf.length); buf = nb;
    let off = 0;
    while(buf.length - off >= 5){
      const t = buf[off], n = ((buf[off+1]<<24)|(buf[off+2]<<16)|(buf[off+3]<<8)|buf[off+4])>>>0;
      if(buf.length - off < 5 + n) break;
      sink(t, buf.subarray(off+5, off+5+n)); off += 5 + n;
    }
    buf = buf.slice(off);
    vFails = 0;
  }}finally{
    sink.dispose?.(); reader.cancel().catch(()=>{});
    if(vAbort===controller) vAbort=null;
  }
}
function onMeta(m){
  vInfo = (m.mode==="virtual" ? "가상 " : "미러 ") + m.w + "x" + m.h + (m.mode==="virtual" ? " flex" : "");
  $("dsel").value = m.mode; badge();
}
function wcSink(){
  const cv = $("vcanvas"), ctx = cv.getContext("2d");
  let dec = null, cfg = null, waitKey = true, config = null, disposed = false;
  let pending = null, paintReq = null, generation = 0, painted = false;
  function clearPending(){
    if(paintReq!==null){ cancelAnimationFrame(paintReq); paintReq=null; }
    if(pending){ pending.close(); pending=null; }
    painted=false;
  }
  function drawPending(){
    const f=pending; pending=null;
    if(!f) return;
    try{
      if(disposed) return;
      if(cv.width!==f.displayWidth || cv.height!==f.displayHeight){
        cv.width=f.displayWidth; cv.height=f.displayHeight;
      }
      ctx.drawImage(f, 0, 0);
      painted=true;
    }catch(e){ if(!disposed) fallback("wc", e.message); }
    finally{ f.close(); }
  }
  function paint(){
    paintReq=null;
    drawPending();
    if(painted){ cv.dataset.frames = (+cv.dataset.frames || 0) + 1; vframe(); painted=false; }
  }
  const sink = (t, p)=>{
    if(disposed) return;
    if(t===1){
      const m = JSON.parse(new TextDecoder().decode(p)); onMeta(m);
      const current=++generation; clearPending(); cfg=null;
      if(dec){ try{ dec.close(); }catch(_){} }
      dec = new VideoDecoder({
        output: f=>{
          if(disposed || current!==generation){ f.close(); return; }
          if(pending) pending.close();
          pending=f;
          if(paintReq===null){
            // Keep the first frame immediate. Only a burst of further outputs
            // waits for repaint; always deferring the first added a frame of lag.
            drawPending();
            if(painted) paintReq=requestAnimationFrame(paint);
          }
        },
        error: e=>{ if(!disposed && current===generation) fallback("wc", e.message); }});
      config={codec: m.codec, optimizeForLatency: true}; dec.configure(config);
      waitKey = true;
    } else if(t===2){ cfg = p.slice();
    } else if(t===3 && dec && dec.state==="configured"){
      const key = p[0]===1;
      const pts = Number(new DataView(p.buffer, p.byteOffset+1, 8).getBigUint64(0));
      let data = p.subarray(9);
      if(dec.decodeQueueSize > 4){
        clearPending();
        dec.reset(); dec.configure(config); waitKey=true; requestVideoKey();
      }
      if(!key && waitKey) return;
      if(key){ waitKey = false;
        if(cfg){ const d = new Uint8Array(cfg.length + data.length); d.set(cfg); d.set(data, cfg.length); data = d; } }
      dec.decode(new EncodedVideoChunk({type: key ? "key" : "delta", timestamp: pts, data}));
    }
  };
  sink.dispose=()=>{ disposed=true; ++generation; clearPending(); if(dec && dec.state!=="closed") dec.close(); };
  return sink;
}
let lastVideoKey = 0;
function requestVideoKey(){
  const now=Date.now(); if(now-lastVideoKey<1000) return; lastVideoKey=now;
  fetch("/api/video/keyframe",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}).catch(()=>{});
}
function mseSink(){
  const v = $("vvideo");
  let ms = null, sb = null, q = [], keyTimes = [], url = null, disposed = false, frameReq = null;
  function presented(){
    if(disposed) return;
    v.dataset.frames=(+v.dataset.frames || 0)+1; vframe();
    frameReq=v.requestVideoFrameCallback(presented);
  }
  if(v.requestVideoFrameCallback) frameReq=v.requestVideoFrameCallback(presented);
  function pump(){
    if(disposed || !sb || sb.updating) return;
    const b = v.buffered;                     // stay at the live edge
    if(b.length){
      const end = b.end(b.length-1);
      if(end - v.currentTime > 0.35) v.currentTime = Math.max(0, end - 0.05);
      // Removing an arbitrary time also removes dependent frames up to the next
      // keyframe. With sparse scrcpy keys that can include the entire live GOP.
      // Keep a known keyframe at least 10s behind playback, including a small
      // margin for MSE's sequence-mode timestamp rounding. If none is known,
      // retain the buffer; old servers without type 4 remain playable.
      let keep = null;
      for(const t of keyTimes){ if(t <= v.currentTime - 10) keep = t; }
      if(keep !== null && b.start(0) < keep - 0.001){
        try{ sb.remove(b.start(0), keep - 0.001); keyTimes = keyTimes.filter(t=>t >= keep); }
        catch(e){ fallback("mse", e.message); }
        return;
      }
    }
    // Prune before appending as a continuous stream may never empty the queue.
    if(q.length){ try{ sb.appendBuffer(q.shift()); }catch(e){ fallback("mse", e.message); } return; }
    if(v.paused) v.play().catch(()=>{});
  }
  function reset(codec){
    if(url) URL.revokeObjectURL(url);
    q = []; sb = null; keyTimes = [];
    ms = new MediaSource(); const current=ms; url=URL.createObjectURL(ms); v.src=url;
    ms.addEventListener("sourceopen", ()=>{
      if(disposed || current!==ms) return;
      try{ sb = ms.addSourceBuffer('video/mp4; codecs="' + codec + '"'); }
      catch(e){ fallback("mse", e.message); return; }
      sb.mode = "sequence";                   // back-to-back: a static screen leaves no gaps
      sb.addEventListener("updateend", pump); pump();
    }, {once: true});
  }
  v.addEventListener("timeupdate", pump);
  const sink = (t, p)=>{
    if(t===1){ const m = JSON.parse(new TextDecoder().decode(p)); onMeta(m); reset(m.codec); }
    else if(t===4){ const m = JSON.parse(new TextDecoder().decode(p));
      if(Number.isFinite(m.key_time) && m.key_time >= 0) keyTimes.push(m.key_time); }
    else if(t===2 || t===3){
      if(q.length>=30) throw new Error("MSE decode backlog");
      q.push(p.slice()); pump(); if(t===3 && !v.requestVideoFrameCallback) vframe();
    }
  };
  sink.dispose=()=>{
    disposed=true; q=[]; v.removeEventListener("timeupdate",pump);
    if(frameReq!==null) v.cancelVideoFrameCallback(frameReq);
    if(sb) sb.removeEventListener("updateend",pump);
    if(url) URL.revokeObjectURL(url);
  };
  return sink;
}
// flex: keep the virtual display matched to the screen area (debounced)
let rzT = null, lastRz = "";
new ResizeObserver(()=>{
  if(renderer==="png") return;
  clearTimeout(rzT);
  rzT = setTimeout(()=>{
    const b = $("conwrap").getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    const k = Math.round(b.width)+"x"+Math.round(b.height)+"@"+dpr;
    if(k===lastRz) return; lastRz = k;
    fetch("/api/video/resize", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({w: b.width, h: b.height, dpr})}).catch(()=>{});
  }, 300);
}).observe($("conwrap"));
$("rsel").value = lsGet("@@STORAGE@@.video", "auto");
$("rsel").onchange = ()=>{ lsSet("@@STORAGE@@.video", $("rsel").value); vFails = 0; startRenderer(wanted()); };
$("qsel").onchange = ()=>{
  cancelPointer();
  fetch("/api/video/quality",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({max_side:Number($("qsel").value)})}).then(r=>r.json()).then(j=>{
      if(j.error) setStatus(j.error); else setStatus("가상 화면 품질: " + j.max_side);
    }).catch(()=>setStatus("품질 변경 실패"));
};
$("dsel").onchange = ()=>{
  fetch("/api/video/mode", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({mode: $("dsel").value})}).then(r=>r.json()).then(j=>{
      setStatus("디스플레이: " + (j.mode==="virtual" ? "가상(flex)" : "미러")); }).catch(()=>{});
};
setInterval(()=>{                                  // surface server-side fallbacks (VD → mirror)
  if(renderer==="png") return;
  fetch("/api/video/info").then(r=>r.json()).then(j=>{
    if(j.note) setStatus(j.note);
    if(j.max_side) $("qsel").value=String(j.max_side);
    $("qsel").disabled=j.mode!=="virtual";
    if(j.mode && $("dsel").value!==j.mode) $("dsel").value = j.mode;
  }).catch(()=>{});
}, 4000);

// ---- startup: the console needs the screen API (local bind or --allow-screen) ----
// Capability check must not start full-resolution PNG capture alongside H.264.
fetch("/api/screen/info?peek=1").then(r=>r.json()).then(j=>{
  if(!j.enabled){
    $("conwrap").innerHTML = '<div class="disabled-note">콘솔 비활성 — 비로컬 바인드에서는 ' +
      '<b>--allow-screen</b> 으로 켜야 합니다(계정 전체 원격 조작이므로 SSH 터널/Tailscale 권장).</div>';
    $("vmode").textContent = "OFF"; return;
  }
  startRenderer(wanted());
  loadPane("android", fp.android.path);
  loadPane("local", fp.local.path);
}).catch(()=>{});
pollState(); setInterval(pollState, 1500);

// keep the layout inside the visible area when a mobile keyboard opens
function fitViewport(){ const vv = window.visualViewport; if(vv) document.body.style.height = vv.height + "px"; }
if(window.visualViewport){
  window.visualViewport.addEventListener("resize", fitViewport);
  window.visualViewport.addEventListener("scroll", fitViewport);
  fitViewport();
}
</script>
</body></html>
"""



def render_page(profile: ConsoleProfile = DEFAULT_PROFILE) -> str:
    import html
    # Profile values are application-owned, but still escape for their contexts.
    tokens = {
        "@@UPLOAD_HEADER@@": "".join(c for c in profile.upload_header if c.isalnum() or c == "-"),
        "@@NAME@@": html.escape(profile.name),
        "@@STORAGE@@": "".join(c for c in profile.storage_prefix if c.isalnum() or c in "-_."),
        "@@HELP_SUFFIX@@": json.dumps(html.escape(profile.help_suffix), ensure_ascii=True)[1:-1].replace("'", "\\'"),
        "@@NAVIGATION@@": ('<button id="ckhome">Home</button><button id="ckrecent">Recent</button>'
                           if profile.navigation else ""),
        "@@MODES@@": "".join('<option value="%s"%s>%s</option>' %
                              (mode, " selected" if mode == profile.default_mode else "", label)
                              for mode, label in (("virtual", "가상"), ("mirror", "미러"))),
    }
    page = _PAGE_TEMPLATE
    for key, value in tokens.items():
        page = page.replace(key, value)
    return page


PAGE = render_page()

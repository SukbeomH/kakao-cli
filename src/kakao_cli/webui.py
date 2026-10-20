"""Web UI — a device console in the browser (stdlib only).

The web UI is console-only: the live device screen in the center (click/drag
→ tap/swipe, keyboard capture → device) and two file managers on the left
(device storage / the webui host's disk) with copy, upload and download.
Chat lives in the CLI/TUI; the web UI no longer has a chat mode.

A single background Worker owns the Driver and serializes UI-touching device
access (PNG capture fallback, adb input, ADBKeyBoard text); file listings and
transfers call adb directly from handler threads (adb's server serializes
transports). No third-party deps, no CDN — the page is inlined.
"""
from __future__ import annotations
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

from .adb import connect
from .worker import Worker
from .screen import ScreenBuffer, view_to_device
from .fs import (LocalRoot, shq, parse_stat_lines, android_list_cmd,
                 android_parent, unique_name, content_disposition, clean_upload_name,
                 host_allowed)

DEFAULT_MAX_UPLOAD = 2 << 30          # 2 GiB (--max-upload)
UPLOAD_HEADROOM = 64 << 20            # keep this much free after an upload

# Android keycodes the console keyboard may forward (everything printable goes
# through ADBKeyBoard text instead). Anything else is rejected.
KEYCODES = {
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

    def snapshot(self) -> dict:
        with self.lock:
            return {"seq": self.seq, "status": self.status}

    def set_status(self, text: str):
        with self.lock:
            if text != self.status:
                self.status = text
                self.seq += 1


def _pump(worker: Worker, state: State):
    while True:
        try:
            kind, data = worker.events.get(timeout=1.0)
        except queue.Empty:
            continue
        if kind == "status":
            state.set_status(data)
        # (chat events don't exist in the console-only web UI)


def make_handler(worker: Worker, state: State, tmpdir: str,
                 screen: ScreenBuffer, screen_enabled: bool, fsroot: LocalRoot,
                 max_upload: int = DEFAULT_MAX_UPLOAD, allow_hosts=(), disk_free=None):
    disk_free = disk_free or (lambda d: shutil.disk_usage(d).free)
    # console file transfers run in their own threads (a large push must not
    # freeze the worker's captures/taps); adb's server serializes transports
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()

    def dev():
        return getattr(getattr(worker, "d", None), "dev", None)

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
        server_version = "kakao-cli-webui"

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
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
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
                if screen_enabled:
                    screen.touch()     # polling info keeps the viewer alive (cheap)
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
            if self.headers.get("X-Kakao-Op") != "upload":
                return self._json(403, {"error": "missing X-Kakao-Op: upload"})
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
            if op in ("tap", "swipe") and (not w or not h):
                return self._json(409, {"error": "no frame yet"})
            if op == "tap":
                x, y = view_to_device(_num(body.get("cx")), _num(body.get("cy")),
                                      _num(body.get("ew")), _num(body.get("eh")), w, h)
                worker.put("tap", x, y)
                self._json(200, {"ok": True, "x": x, "y": y})
            elif op == "swipe":
                ew, eh = _num(body.get("ew")), _num(body.get("eh"))
                x1, y1 = view_to_device(_num(body.get("cx1")), _num(body.get("cy1")), ew, eh, w, h)
                x2, y2 = view_to_device(_num(body.get("cx2")), _num(body.get("cy2")), ew, eh, w, h)
                ms = max(50, min(3000, int(_num(body.get("ms"), 200))))
                worker.put("swipe", x1, y1, x2, y2, ms)
                self._json(200, {"ok": True})
            elif op == "key":
                name, code = body.get("name"), body.get("code")
                if name in ("back", "home", "enter"):
                    worker.put("key", name)
                elif isinstance(code, int) and not isinstance(code, bool) and code in KEYCODES:
                    worker.put("key", code)       # keyboard passthrough (allow-listed)
                else:
                    return self._json(400, {"error": "bad key"})
                self._json(200, {"ok": True})
            elif op == "text":
                text = body.get("text") or ""
                if not text:
                    return self._json(400, {"error": "empty text"})
                worker.put("screentext", text)
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})

    return Handler


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
        return float(v)
    except (TypeError, ValueError):
        return default


def serve(worker: Worker, host: str, port: int, tmpdir: str = None,
          screen: ScreenBuffer = None, screen_enabled: bool = True,
          fs_root: str = "~", max_upload: int = DEFAULT_MAX_UPLOAD,
          allow_hosts=(), disk_free=None) -> ThreadingHTTPServer:
    """Build the server (worker already startable). Caller runs serve_forever."""
    state = State()
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="kakao-webui-")
    if screen is None:
        screen = ScreenBuffer()
    threading.Thread(target=_pump, args=(worker, state), daemon=True).start()
    httpd = ThreadingHTTPServer(
        (host, port), make_handler(worker, state, tmpdir, screen, screen_enabled,
                                   LocalRoot(fs_root), max_upload=max_upload,
                                   allow_hosts=tuple(allow_hosts), disk_free=disk_free))
    httpd.daemon_threads = True
    return httpd


def _is_local(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "")


def run(serial=None, addr=None, adb="adb", host="127.0.0.1", port=8800, allow_screen=False,
        fs_root="~", max_upload=DEFAULT_MAX_UPLOAD, allow_hosts=()):
    dev = connect(serial, addr, adb=adb)
    tmpdir = tempfile.mkdtemp(prefix="kakao-webui-")
    screen = ScreenBuffer()
    # the screen view / console is full-account remote control; off by default on
    # non-local binds unless explicitly allowed
    screen_enabled = _is_local(host) or allow_screen
    worker = Worker(dev, screen=screen, chat=False)
    worker.start()
    httpd = serve(worker, host, port, tmpdir=tmpdir, screen=screen,
                  screen_enabled=screen_enabled, fs_root=fs_root,
                  max_upload=max_upload, allow_hosts=allow_hosts)
    shown = host if host not in ("0.0.0.0", "") else "<vm-ip>"
    print(f"kakao-cli webui on http://{shown}:{port}  (Ctrl-C to stop)")
    print(f"  screen view / console (files): {'ON' if screen_enabled else 'OFF'}"
          f"  — local file root: {LocalRoot(fs_root).root}")
    if not _is_local(host):
        print("  WARNING: bound to all interfaces with no auth — anyone on the "
              "network can control this KakaoTalk account.")
        if not screen_enabled:
            print("  screen view/control is OFF (non-local bind); pass --allow-screen "
                  "to enable it (even higher risk).")
        print("  Prefer the default 127.0.0.1 + an SSH tunnel: "
              f"ssh -L {port}:127.0.0.1:{port} <vm>")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        worker.put("quit")


# --- the single page: device console in the TUI tone (monospace, dark, reverse-video bars) ---
PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#16161e">
<title>kakao-cli 콘솔</title>
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
  .console{flex:1 1 auto;display:flex;min-height:0}
  .side{flex:0 0 34%;max-width:460px;min-width:260px;display:flex;flex-direction:column;
    border-right:1px solid var(--line);min-height:0}
  .fpane{flex:1 1 50%;display:flex;flex-direction:column;min-height:0;border-bottom:1px solid var(--line)}
  .fhead{display:flex;align-items:center;gap:6px;padding:4px 8px;background:var(--bar)}
  .fhead b{color:var(--accent);white-space:nowrap;font-size:13px}
  .fhead input{flex:1;min-width:0;background:transparent;border:1px solid var(--line);
    border-radius:3px;color:var(--fg);font-family:inherit;font-size:13px;padding:2px 6px;outline:none}
  .fhead .fbtn{color:var(--accent);border:1px solid var(--line);border-radius:3px;padding:1px 8px;
    white-space:nowrap;font-size:12px}
  .flist{flex:1 1 auto;overflow-y:auto;font-size:13px}
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
  .job{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .job.running{color:var(--unread)} .job.done{color:var(--me)} .job.error{color:#f7768e}
  .center{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;min-height:0;padding:6px}
  .scrbar{display:flex;gap:6px;align-items:center;padding:4px 0;flex:0 0 auto}
  .scrbar button{color:var(--accent);border:1px solid var(--line);border-radius:4px;padding:6px 10px;min-height:36px}
  .scrbar button:hover{background:var(--line)}
  .scrbar .fps{margin-left:auto;color:var(--dim);font-size:12px;white-space:nowrap}
  .kbhint{color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .scrwrap{flex:1 1 auto;min-height:0;display:flex;align-items:center;justify-content:center;
    background:#000;border:1px solid var(--line);border-radius:4px;position:relative}
  .scrwrap.kbon{box-shadow:0 0 0 2px var(--accent) inset}
  #scr{max-width:100%;max-height:100%;object-fit:contain;touch-action:none;
    user-select:none;-webkit-user-select:none}
  #kbcap{position:absolute;left:0;top:0;width:1px;height:1px;opacity:0;border:0;padding:0;
    resize:none;font-size:16px}
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
    .side{flex:1 1 45%;max-width:none;min-width:0;border-right:none}
    .center{flex:0 0 55%;order:-1}
    .scrbar button{min-height:40px}
  }
</style></head>
<body>
  <div class="bar" id="header"><span class="hint"><span class="k">kakao-cli</span> 콘솔<span class="long"> —
    화면 클릭: 탭·키보드 · 드래그: 스와이프 · Esc: 키보드 해제</span></span>
    <button id="helpbtn" title="도움말 (?)">?</button></div>
  <div class="console" id="console">
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
    <div class="center">
      <div class="scrbar">
        <button id="ckback">Back</button><button id="ckhome">Home</button><button id="ckenter">Enter</button>
        <span class="kbhint" id="kbhint">화면을 클릭하면 키보드가 기기로 갑니다 · Esc 해제</span>
        <span class="fps" id="cfps"></span>
      </div>
      <div class="scrwrap" id="conwrap"><textarea id="kbcap" autocomplete="off" spellcheck="false"
        aria-label="기기 키보드 입력"></textarea><img id="scr" alt="기기 화면" draggable="false"></div>
    </div>
  </div>
  <div class="bar statusbar" id="statusbar">
    <span id="status" role="status">starting …</span>
    <span class="badge" id="vmode" title="화면 전달 방식">PNG</span>
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
  '<b>드래그</b>  스와이프\n' +
  '<b>키보드</b>  글자는 ADBKeyBoard(한글 OK), Backspace·Enter·Tab·방향키·Home/End·PgUp/PgDn·Delete\n' +
  '<b>Esc</b>  키보드 해제 · <b>Back/Home/Enter</b> 버튼\n' +
  '<b>⇪ 올리기 / 드래그앤드롭</b>  브라우저 파일을 그 패널 폴더로\n' +
  '<b>⤓ / 더블클릭</b>  파일을 브라우저로 받기\n' +
  '<b>↓ 로컬로 / ↑ 안드로이드로</b>  기기 ⇄ VM 복사(덮어쓰지 않음)\n' +
  '<b>?</b>  이 도움말 · 채팅은 CLI/TUI(<b>kakao tui</b>)에서';
$("helptext").innerHTML = HELP;
function openHelp(){ $("helpovl").classList.add("show"); }
function closeHelp(){ $("helpovl").classList.remove("show"); }
$("helpbtn").onclick = openHelp;
$("helpovl").addEventListener("click", e=>{ if(e.target.id==="helpovl") closeHelp(); });
document.addEventListener("keydown", e=>{
  const t = e.target;
  if(t.tagName==="INPUT" || t.tagName==="TEXTAREA") return;   // path boxes / kbcap
  if(e.key==="Escape"){ closeHelp(); return; }
  if(e.key==="?"){ e.preventDefault(); openHelp(); }
});
let tempStatus = 0;
function setStatus(s){ $("status").textContent = " "+s; tempStatus = Date.now(); }

// ---- server status line (worker errors etc.) ----
let stSeq = -1;
async function pollState(){
  try{
    const r = await fetch("/api/state?since="+stSeq);
    if(r.status===200){ const j = await r.json(); stSeq = j.seq;
      if(Date.now()-tempStatus > 4000) $("status").textContent = " "+(j.status||""); }
  }catch(e){}
}

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
        if(now-fpsT >= 1000){ $("cfps").textContent = fpsCount+" fps"; fpsCount=0; fpsT=now; }
      };
      nx.src = "/api/screen/frame?seq=" + j.seq;
    }
    setTimeout(pollFrame, 250);
  }).catch(()=>{ if(pngWanted) setTimeout(pollFrame, 600); else frameLoop=false; });
}
async function scrPost(path, body){
  try{ await fetch("/api/screen/"+path, {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}); }catch(e){}
}
const scr = $("scr");
let pd = null;
scr.addEventListener("pointerdown", e=>{ e.preventDefault(); pd={x:e.offsetX,y:e.offsetY,t:Date.now()};
  try{ scr.setPointerCapture(e.pointerId); }catch(_){}
  $("kbcap").focus({preventScroll:true}); });  // clicking the screen routes the keyboard to the device
scr.addEventListener("pointerup", e=>{
  if(!pd) return;
  const ew=scr.clientWidth, eh=scr.clientHeight;
  const dx=e.offsetX-pd.x, dy=e.offsetY-pd.y;
  if(Math.hypot(dx,dy) > 12){
    scrPost("swipe",{cx1:pd.x,cy1:pd.y,cx2:e.offsetX,cy2:e.offsetY,ew,eh,ms:Math.max(120,Date.now()-pd.t)});
  } else {
    scrPost("tap",{cx:pd.x,cy:pd.y,ew,eh});
  }
  pd=null;
});

$("ckback").onclick = ()=> scrPost("key",{name:"back"});
$("ckhome").onclick = ()=> scrPost("key",{name:"home"});
$("ckenter").onclick = ()=> scrPost("key",{name:"enter"});

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
function uploadOne(which, f){
  return new Promise(resolve=>{
    const P = fp[which], dir = P.path || "";
    const job = {state:"running", label:"⇪ "+f.name+(which==="android"?" → 기기":" → VM"), note:"0%"};
    jobs.push(job); renderJobs();
    const q = (which==="android" ? "target=android&" : "") + "dir="+encodeURIComponent(dir) +
              "&name="+encodeURIComponent(f.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/fs/upload?"+q);
    xhr.setRequestHeader("X-Kakao-Op", "upload");
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
const KMAP = {Backspace:67, Enter:66, Tab:61, ArrowUp:19, ArrowDown:20, ArrowLeft:21,
              ArrowRight:22, Delete:112, Home:122, End:123, PageUp:92, PageDown:93};
function kbFlush(){
  if(kbT){ clearTimeout(kbT); kbT=null; }
  if(kbuf){ const t=kbuf; kbuf=""; scrPost("text", {text:t}); }
}
function kbQueue(t){                 // batch keystrokes: one ADBKeyBoard broadcast per ~150ms
  if(!t) return;
  kbuf += t;
  if(kbT) clearTimeout(kbT);
  kbT = setTimeout(kbFlush, 150);
}
kbcap.addEventListener("keydown", e=>{
  e.stopPropagation();
  if(e.isComposing || e.keyCode===229) return;           // Korean syllable still composing
  if(e.key==="Escape"){ e.preventDefault(); kbcap.blur(); return; }
  if(e.ctrlKey || e.metaKey || e.altKey) return;
  const code = KMAP[e.key];
  if(code!==undefined){ e.preventDefault(); kbFlush(); scrPost("key", {code}); }
});
kbcap.addEventListener("input", e=>{
  if(e.isComposing) return;                              // wait for compositionend
  const v = kbcap.value; kbcap.value = ""; kbQueue(v);
});
kbcap.addEventListener("compositionend", ()=>{
  const v = kbcap.value; kbcap.value = ""; kbQueue(v);   // committed syllables only
});
kbcap.addEventListener("focus", ()=> $("conwrap").classList.add("kbon"));
kbcap.addEventListener("blur", ()=>{ kbFlush(); $("conwrap").classList.remove("kbon"); });


// ---- startup: the console needs the screen API (local bind or --allow-screen) ----
fetch("/api/screen/info").then(r=>r.json()).then(j=>{
  if(!j.enabled){
    $("conwrap").innerHTML = '<div class="disabled-note">콘솔 비활성 — 비로컬 바인드에서는 ' +
      '<b>--allow-screen</b> 으로 켜야 합니다(계정 전체 원격 조작이므로 SSH 터널/Tailscale 권장).</div>';
    $("vmode").textContent = "OFF"; return;
  }
  startFrames();
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

"""Web UI — the TUI's look and feel in a browser (stdlib only).

Same shape as the curses TUI: a single background :class:`~kakao_cli.worker.Worker`
owns the Driver and serializes device access; the HTTP layer only pushes commands
in and serves a lock-guarded snapshot of state out. No third-party deps, no CDN —
the page (HTML/CSS/JS) is inlined and served from memory.

Endpoints:
  GET  /                      the single-page UI
  GET  /api/state?since=N     current snapshot (204 if seq still == N)
  POST /api/open   {"room"}   open a room
  POST /api/send   {"text"}   send into the open room (400 if none open)
  POST /api/rooms             refresh the room list
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
    """Lock-guarded snapshot the HTTP layer serves; fed by the event pump.

    Mirrors the TUI's state transitions exactly: opening a room clears the view
    and leaves ``room`` None until the worker's ``opened`` event, and a
    ``messages`` event for a room other than the current one is dropped — so the
    previous room's log never flashes into the new one.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.rooms: list[dict] = []
        self.room: str | None = None   # confirmed-open room
        self.pending: str | None = None  # room we asked to open
        self.messages: list[dict] = []
        self.status = "starting …"
        self.login_needed = False
        self.exports: dict[str, dict] = {}     # export id -> {ready, token?, name?, error?}
        self.downloads: dict[str, dict] = {}   # token -> {path, name, mime}

    def _bump(self):
        self.seq += 1

    def snapshot(self) -> dict:
        with self.lock:
            # serve messages without device-pixel bounds the browser doesn't need
            msgs = [{k: v for k, v in m.items() if k != "bounds"}
                    if isinstance(m, dict) else {"kind": "text", "text": m}
                    for m in self.messages]
            return {"seq": self.seq, "rooms": self.rooms, "room": self.room,
                    "messages": msgs, "status": self.status,
                    "login_needed": self.login_needed}

    def export_pending(self, xid: str):
        with self.lock:
            self.exports[xid] = {"ready": False}

    def export_done(self, xid: str, path: str, name: str, mime: str) -> str:
        import uuid
        token = uuid.uuid4().hex
        with self.lock:
            self.downloads[token] = {"path": path, "name": name, "mime": mime}
            self.exports[xid] = {"ready": True, "token": token, "name": name, "mime": mime}
        return token

    def export_failed(self, xid: str, error: str):
        with self.lock:
            self.exports[xid] = {"ready": False, "error": error}

    def export_status(self, xid: str) -> dict | None:
        with self.lock:
            e = self.exports.get(xid)
            return dict(e) if e else None

    def download(self, token: str) -> dict | None:
        with self.lock:
            d = self.downloads.get(token)
            return dict(d) if d else None

    def has_target(self) -> bool:
        """A room is open, or an open is in flight. Commands queue behind that
        open (open is enqueued first), so send/sendfile are safe to accept —
        this avoids a 400 during the brief want_open gap when a row is reclicked."""
        with self.lock:
            return bool(self.room or self.pending)

    def want_open(self, room: str):
        with self.lock:
            self.pending = room
            self.room = None
            self.messages = []
            self._bump()

    def apply(self, kind, data):
        with self.lock:
            # only bump seq on a real change — the worker re-emits identical
            # rooms/status/messages every poll, and bumping each time would make
            # the browser rebuild the DOM ~every 2.5s (flicker, scroll yank,
            # lost input focus). Dedupe here so unchanged polls stay 204.
            if kind == "rooms":
                if data == self.rooms:
                    return
                self.rooms = data
            elif kind == "status":
                if data == self.status:
                    return
                self.status = data
            elif kind == "opened":
                if data == self.room and self.pending is None:
                    return
                self.room = data
                self.pending = None
            elif kind == "messages":
                room, msgs = data
                if room != self.room:
                    return  # stale (previous room) — don't bump
                if msgs == self.messages:
                    return
                self.messages = msgs
            elif kind == "login":
                if data == self.login_needed:
                    return
                self.login_needed = data
            self._bump()


def _pump(worker: Worker, state: State):
    while True:
        try:
            kind, data = worker.events.get(timeout=1.0)
        except queue.Empty:
            continue
        if kind == "export_result":
            state.export_done(data["id"], data["path"], data["name"], data["mime"])
        elif kind == "export_error":
            state.export_failed(data["id"], data.get("error", "export failed"))
        else:
            state.apply(kind, data)


def _safe_name(raw: str) -> str:
    """Browser filename -> a shell/picker-safe basename, keeping the extension.
    `\\w` keeps Hangul; drop path separators and anything that could break the
    unquoted `am broadcast -d file://…` / DocumentsUI search in send_doc."""
    base = os.path.basename(unquote(raw or "")) or "file"
    safe = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "file"
    return safe


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
            elif path.startswith("/api/export/"):
                xid = path[len("/api/export/"):]
                st = state.export_status(xid)
                if st is None:
                    return self._json(404, {"error": "unknown export"})
                self._json(200, st)
            elif path.startswith("/api/download/"):
                token = path[len("/api/download/"):]
                d = state.download(token)
                if not d or not os.path.exists(d["path"]):
                    return self._json(404, {"error": "expired"})
                self._serve_file(d["path"], d["name"], d["mime"])
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
            if self.path == "/api/open":
                room = (self._read_json().get("room") or "").strip()
                if not room:
                    return self._json(400, {"error": "room required"})
                state.want_open(room)
                worker.put("open", room)
                self._json(200, {"ok": True, "room": room})
            elif self.path == "/api/send":
                text = self._read_json().get("text") or ""
                if not state.has_target():
                    return self._json(400, {"error": "no room open"})
                if not text.strip():
                    return self._json(400, {"error": "empty text"})
                worker.put("send", text)
                self._json(200, {"ok": True})
            elif self.path.split("?", 1)[0] == "/api/sendfile":
                if not state.has_target():
                    return self._json(400, {"error": "no room open"})
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0:
                    return self._json(400, {"error": "empty body"})
                name = ""
                if "?" in self.path:
                    q = parse_qs(self.path.split("?", 1)[1])
                    name = (q.get("name") or [""])[0]
                safe = _safe_name(name)
                dest = os.path.join(tmpdir, f"{int(time.time() * 1000)}_{safe}")
                remaining = n
                with open(dest, "wb") as f:  # stream — never buffer a whole video
                    while remaining > 0:
                        chunk = self.rfile.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                worker.put("sendfile", dest)
                self._json(200, {"ok": True, "name": safe})
            elif self.path == "/api/export":
                if not state.has_target():
                    return self._json(400, {"error": "no room open"})
                body = self._read_json()
                kind = body.get("kind")
                if kind not in ("photo", "video", "file"):
                    return self._json(400, {"error": "bad kind"})
                rank = body.get("rank")
                name = body.get("name") or ""
                import uuid
                xid = uuid.uuid4().hex
                state.export_pending(xid)
                worker.put("export", xid, kind, rank if isinstance(rank, int) else -1, name)
                self._json(202, {"id": xid})
            elif self.path == "/api/rooms":
                worker.put("rooms")
                self._json(200, {"ok": True})
            elif self.path.startswith("/api/screen/"):
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
    worker = Worker(dev, export_dir=tmpdir, screen=screen)
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


# --- the single page: TUI tone & manner (monospace, dark, reverse-video bars),
# responsive down to phones (single-screen list↔conversation) ---
PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#16161e">
<title>kakao-cli</title>
<style>
  :root{
    --bg:#16161e; --fg:#c0caf5; --dim:#565f89; --bar:#2a2e42; --barfg:#c0caf5;
    --sel:#3d59a1; --selfg:#ffffff; --accent:#7aa2f7; --unread:#e0af68;
    --line:#2a2e42; --me:#9ece6a;
    --safe-b:env(safe-area-inset-bottom,0px);
    --safe-t:env(safe-area-inset-top,0px);
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
  #header{padding-top:calc(4px + var(--safe-t))}
  .main{flex:1 1 auto;display:flex;min-height:0}
  .list{flex:0 0 32%;max-width:360px;min-width:200px;overflow-y:auto;
    border-right:1px solid var(--line);padding:4px 0;-webkit-overflow-scrolling:touch}
  .row{padding:5px 10px;cursor:pointer;overflow:hidden}
  .row:hover{background:var(--line)}
  .row.sel{background:var(--sel);color:var(--selfg)}
  .row .r1{display:flex;gap:6px;align-items:baseline}
  .row .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .row.unread .nm{font-weight:bold;color:var(--unread)}
  .row.sel.unread .nm{color:var(--selfg)}
  .row .mc{color:var(--dim);font-weight:normal}
  .row.sel .mc{color:var(--selfg)}
  .row .tm{color:var(--dim);font-size:12px;white-space:nowrap}
  .row.sel .tm{color:var(--selfg)}
  .row .r2{display:flex;gap:6px;align-items:baseline;margin-top:1px}
  .row .pv{flex:1;color:var(--dim);white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;font-size:13px}
  .row.sel .pv{color:var(--selfg)}
  .row .badge{color:var(--unread);font-weight:bold;white-space:nowrap}
  .row.sel .badge{color:var(--selfg)}
  .row .mute{color:var(--dim)}
  .pane{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;position:relative}
  .title{display:flex;align-items:center;gap:6px;padding:4px 12px;color:var(--accent);
    font-weight:bold;border-bottom:1px solid var(--line)}
  .title #titletext{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
  .back{display:none;color:var(--accent);font-weight:bold;padding:4px 8px;
    min-width:36px;border-radius:4px}
  .back:hover{background:var(--line)}
  .msgs{flex:1 1 auto;overflow-y:auto;padding:6px 12px;-webkit-overflow-scrolling:touch;
    position:relative}
  .msg{display:flex;align-items:baseline;gap:6px;padding:1px 0;word-break:break-word}
  .msg .who{flex:0 0 8ch;max-width:8ch;text-align:right;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;color:var(--accent)}
  .msg .sep{color:var(--line)}
  .msg .body{flex:1;min-width:0;white-space:pre-wrap}
  .msg.me .who{color:var(--me)}
  .msg.me .body{color:var(--me)}
  .dlbtn{color:inherit;text-align:left;padding:0;cursor:pointer}
  .dlbtn:hover{text-decoration:underline}
  .dlbtn .dlico{color:var(--accent)}
  .dlbtn.dl-busy{opacity:.55}
  .dlbtn.dl-busy .dlico::after{content:" …"}
  .daterow{position:sticky;top:0;z-index:3;text-align:center;color:var(--dim);
    white-space:pre;padding:4px 0;background:var(--bg);font-size:12px}
  .newmsg{position:absolute;left:50%;transform:translateX(-50%);bottom:64px;
    display:none;background:var(--sel);color:var(--selfg);border-radius:14px;
    padding:4px 12px;box-shadow:0 1px 6px rgba(0,0,0,.4);z-index:5}
  .newmsg.show{display:block}
  .inputline{display:flex;align-items:center;gap:6px;padding:4px 12px;
    border-top:1px solid var(--line);padding-bottom:calc(4px + var(--safe-b))}
  .inputline .prompt{color:var(--accent)}
  .inputline.blur .prompt{color:var(--dim)}
  .attach{color:var(--accent);font-weight:bold;user-select:none;
    min-width:32px;text-align:center}
  .attach:hover{color:var(--unread)}
  .inputline #input{flex:1;background:transparent;border:none;color:var(--fg);
    font-family:inherit;font-size:16px;line-height:1.4;outline:none;min-width:0;
    resize:none;max-height:40vh;overflow-y:auto}
  .send{color:var(--accent);font-weight:bold;padding:4px 10px;border-radius:4px;
    align-self:flex-end}
  .send:hover{background:var(--line)}
  .empty{color:var(--dim);padding:8px 12px}
  .inputline.drop{outline:2px dashed var(--accent);outline-offset:-4px}
  .scrbtn{color:var(--accent);padding:4px 8px;border-radius:4px;white-space:nowrap}
  .scrbtn:hover{background:var(--line)}
  .statusbar{display:flex;align-items:center;gap:8px}
  .statusbar #status{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .statusbar .scrbtn{min-height:32px}
  .banner{background:#4d3b1a;color:var(--unread);padding:6px 12px;
    border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center}
  .banner[hidden]{display:none}
  .banner button{color:var(--accent);font-weight:bold;border:1px solid var(--accent);
    border-radius:4px;padding:2px 8px}
  /* live device screen overlay */
  .screenovl .scrpanel{max-width:520px;margin:0 auto;height:100%;display:flex;
    flex-direction:column;padding:8px}
  .scrbar{display:flex;gap:6px;align-items:center;padding:4px 0;flex:0 0 auto}
  .scrbar button{color:var(--accent);border:1px solid var(--line);border-radius:4px;
    padding:6px 10px;min-height:40px}
  .scrbar button:hover{background:var(--line)}
  .scrbar .fps{margin-left:auto;color:var(--dim);font-size:12px}
  .scrwrap{flex:1 1 auto;min-height:0;display:flex;align-items:center;justify-content:center;
    background:#000;border:1px solid var(--line);border-radius:4px}
  #scr{max-width:100%;max-height:100%;object-fit:contain;touch-action:none;
    user-select:none;-webkit-user-select:none}
  .scrinput{display:flex;gap:6px;padding:6px 0;flex:0 0 auto;
    padding-bottom:calc(6px + var(--safe-b))}
  .scrinput input{flex:1;background:transparent;border:1px solid var(--line);
    border-radius:4px;color:var(--fg);font-family:inherit;font-size:16px;padding:8px;outline:none}
  .scrinput button{color:var(--accent);border:1px solid var(--line);border-radius:4px;padding:0 14px;min-height:40px}

  /* overlays: quick switcher + help (unicode/text only, TUI tone) */
  .ovl{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;z-index:20;
    padding-top:10vh}
  .ovl.show{display:block}
  .panel{max-width:560px;margin:0 auto;background:var(--bg);border:1px solid var(--line);
    border-radius:6px;overflow:hidden;box-shadow:0 6px 30px rgba(0,0,0,.5)}
  .panel .qin{width:100%;background:transparent;border:none;border-bottom:1px solid var(--line);
    color:var(--fg);font-family:inherit;font-size:16px;padding:10px 12px;outline:none}
  .panel .res{max-height:50vh;overflow-y:auto}
  .panel .res .row{padding:8px 12px}
  .panel .help{padding:12px 16px;line-height:1.8;white-space:pre-wrap}
  .panel .help b{color:var(--accent)}
  .panel h3{margin:0;padding:10px 12px;color:var(--accent);border-bottom:1px solid var(--line)}

  /* mode switch (채팅 | 콘솔) */
  #header{display:flex;align-items:center;gap:10px}
  #header .hint{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .modes{display:flex;gap:2px;flex:0 0 auto}
  .modes button{padding:0 10px;border-radius:3px;color:var(--dim)}
  .modes button.on{background:var(--sel);color:var(--selfg)}
  .modes button:disabled{opacity:.4;cursor:default}

  /* console: file managers (left, split) + live device screen (center) */
  .console{flex:1 1 auto;display:none;min-height:0}
  body[data-mode="console"] .main{display:none}
  body[data-mode="console"] .console{display:flex}
  .side{flex:0 0 34%;max-width:460px;min-width:260px;display:flex;flex-direction:column;
    border-right:1px solid var(--line);min-height:0}
  .fpane{flex:1 1 50%;display:flex;flex-direction:column;min-height:0;
    border-bottom:1px solid var(--line)}
  .fhead{display:flex;align-items:center;gap:6px;padding:4px 8px;background:var(--bar)}
  .fhead b{color:var(--accent);white-space:nowrap;font-size:13px}
  .fhead input{flex:1;min-width:0;background:transparent;border:1px solid var(--line);
    border-radius:3px;color:var(--fg);font-family:inherit;font-size:13px;padding:2px 6px;outline:none}
  .flist{flex:1 1 auto;overflow-y:auto;font-size:13px}
  .frow{display:flex;gap:8px;padding:3px 10px;cursor:pointer;white-space:nowrap}
  .frow:hover{background:var(--line)}
  .frow .fn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
  .frow .fs{color:var(--dim)}
  .frow.dir .fn{color:var(--accent)}
  .frow.sel{background:var(--sel);color:var(--selfg)}
  .frow.sel .fn,.frow.sel .fs{color:var(--selfg)}
  .ffoot{display:flex;gap:6px;padding:4px 8px;border-top:1px solid var(--line);align-items:center}
  .ffoot .fsel{flex:1;min-width:0;color:var(--dim);font-size:12px;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  .ffoot button{color:var(--accent);border:1px solid var(--line);border-radius:3px;
    padding:3px 10px;white-space:nowrap}
  .ffoot button:disabled{color:var(--dim);cursor:default}
  .ferr{color:#f7768e;padding:6px 10px;font-size:12px;white-space:normal}
  .fhead .fbtn{color:var(--accent);border:1px solid var(--line);border-radius:3px;padding:1px 8px;
    white-space:nowrap;font-size:12px}
  .frow .fdl{color:var(--accent);padding:0 4px;visibility:hidden}
  .frow:hover .fdl,.frow.sel .fdl{visibility:visible}
  @media (hover:none){ .frow .fdl{visibility:visible} }
  .fpane.drop{outline:2px dashed var(--accent);outline-offset:-3px}
  .jobs{flex:0 0 auto;max-height:22%;overflow-y:auto;font-size:12px;padding:2px 8px}
  .job{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .job.running{color:var(--unread)} .job.done{color:var(--me)} .job.error{color:#f7768e}
  .center{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;min-height:0;padding:6px}
  .center .scrwrap{position:relative}
  .center .scrwrap.kbon{box-shadow:0 0 0 2px var(--accent) inset}
  .kbhint{color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #kbcap{position:absolute;left:0;top:0;width:1px;height:1px;opacity:0;border:0;
    padding:0;resize:none;font-size:16px}

  /* phones / narrow: single-screen navigation (list <-> conversation) */
  @media (max-width:720px){
    body[data-mode="console"] .console{flex-direction:column}
    .side{flex:1 1 45%;max-width:none;min-width:0;border-right:none}
    .center{flex:0 0 55%;order:-1}
    #header{display:none}
    .list{flex:1 1 auto;max-width:none;min-width:0;border-right:none}
    .pane{flex:1 1 auto}
    .row{min-height:44px;display:flex;flex-direction:column;justify-content:center}
    .back{display:inline-block}
    .attach{min-width:44px;min-height:44px;line-height:44px;font-size:22px}
    .send{min-height:44px;padding:0 14px}
    .title{min-height:44px}
    body[data-view="list"] .pane{display:none}
    body[data-view="convo"] .list{display:none}
  }
  @media (hover:none){ .msg.dl:active{opacity:.6} .row:active{background:var(--line)} }
</style></head>
<body data-view="list" data-mode="chat">
  <div class="bar" id="header"><span class="hint" id="hint"></span>
    <span class="modes" role="tablist" aria-label="모드">
      <button id="m-chat" class="on" role="tab">채팅</button>
      <button id="m-console" role="tab" title="콘솔: 기기 화면 + 파일 (c)">콘솔</button>
    </span></div>
  <div class="banner" id="loginbanner" hidden>
    로그인이 필요합니다 — <button id="loginopen">화면 열기</button>
  </div>
  <div class="main">
    <div class="list" id="list" role="listbox" aria-label="채팅방 목록"></div>
    <div class="pane">
      <div class="title bar" id="title">
        <button class="back" id="back" aria-label="목록으로">←</button>
        <span id="titletext">(select a room)</span>
        <button class="scrbtn" id="scrbtn" title="기기 화면 (g)" aria-label="기기 화면">⧉ 화면</button>
      </div>
      <div class="msgs" id="msgs" role="log" aria-live="polite"><div class="empty">—</div></div>
      <button class="newmsg" id="newmsg" aria-label="새 메시지로 이동">새 메시지 ↓</button>
      <div class="inputline blur" id="inputline">
        <button class="attach" id="attach" title="파일/이미지 첨부" aria-label="첨부">+</button>
        <input type="file" id="file" hidden>
        <span class="prompt">&gt;</span>
        <textarea id="input" rows="1" autocomplete="off" spellcheck="false"
               enterkeyhint="send"
               placeholder="메시지 입력 (Enter 전송, Shift+Enter 줄바꿈)"></textarea>
        <button class="send" id="send">보내기</button>
      </div>
    </div>
  </div>
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
          <input id="fpath-local" spellcheck="false" autocomplete="off" aria-label="로컬 경로"
                 placeholder="(루트)">
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
        aria-label="기기 키보드 입력"></textarea></div>
    </div>
  </div>
  <div class="bar statusbar" id="statusbar">
    <span id="status" role="status">starting …</span>
    <button class="scrbtn" id="mconsole" title="콘솔/채팅 전환 (c)">⌨ 콘솔</button>
    <button class="scrbtn" id="scrbtn2" title="기기 화면 (g)" aria-label="기기 화면">⧉ 화면</button>
  </div>

  <div class="ovl" id="switcher">
    <div class="panel">
      <input class="qin" id="qin" placeholder="방 이름 필터 — ↑↓ 이동, Enter 열기, Esc 닫기"
             autocomplete="off" spellcheck="false">
      <div class="res" id="qres" role="listbox" aria-label="방 빠른 전환"></div>
    </div>
  </div>
  <div class="ovl" id="helpovl">
    <div class="panel">
      <h3>키 도움말</h3>
      <div class="help" id="helptext"></div>
    </div>
  </div>
  <div class="ovl screenovl" id="screenovl">
    <div class="scrpanel">
      <div class="scrbar">
        <button id="scrclose">✕ 닫기</button>
        <button id="kbback">Back</button>
        <button id="kbhome">Home</button>
        <button id="kbenter">Enter</button>
        <span class="fps" id="fps"></span>
      </div>
      <div class="scrwrap"><img id="scr" alt="기기 화면" draggable="false"></div>
      <div class="scrinput">
        <input id="scrtext" type="text" placeholder="화면에 텍스트 입력(한글 OK)"
               autocomplete="off" spellcheck="false">
        <button id="scrsend">입력</button>
      </div>
    </div>
  </div>
<script>
"use strict";
const HINT = '<span class="k">kakao-cli</span> — ' +
  '<span class="k">Tab</span>: focus  ' +
  '<span class="k">↑↓/Enter</span>: room  ' +
  '<span class="k">Enter</span>: send  ' +
  '<span class="k">/ or Ctrl+K</span>: 전환  ' +
  '<span class="k">?</span>: 도움말';
const HELP =
  '<b>Tab</b>  목록 ↔ 입력 포커스 전환\n' +
  '<b>↑ ↓</b>  목록에서 방 이동 · <b>Enter</b> 열기\n' +
  '<b>Enter</b>  전송 · <b>Shift+Enter</b> 줄바꿈\n' +
  '<b>/ · Ctrl+K</b>  빠른 방 전환(퍼지 필터)\n' +
  '<b>Alt+↑ / Alt+↓</b>  이전 / 다음 방\n' +
  '<b>Alt+Shift+↑/↓</b>  이전 / 다음 안읽음 방\n' +
  '<b>Esc</b>  목록으로 · <b>r</b> 새로고침 · <b>+</b> 파일\n' +
  '<b>m</b>  현재 방 조용히(알림/배지 제외) 토글\n' +
  '<b>n</b>  탭이 숨겨졌을 때 알림 켜기/끄기\n' +
  '<b>g</b>  기기 화면 보기·조작(로그인용)\n' +
  '<b>c</b>  콘솔 ↔ 채팅 전환 (콘솔: 기기 화면 + 파일 복사)\n' +
  '  콘솔에서 화면 클릭 → 키보드가 기기로 (한글 OK), <b>Esc</b> 해제\n' +
  '<b>?</b>  이 도움말';
const $ = id => document.getElementById(id);
$("hint").innerHTML = HINT;

let st = {seq:-1, rooms:[], room:null, messages:[], status:""};
let sel = 0, focus = "list", view = "list", mode = "chat";
const input = $("input");

const esc = s => (s||"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const isMobile = () => window.matchMedia("(max-width:720px)").matches;

// ---- client-side prefs (localStorage): quiet rooms, drafts, notifications ----
const lsGet = (k,d) => { try{ return JSON.parse(localStorage.getItem(k)) ?? d; }catch(e){ return d; } };
const lsSet = (k,v) => { try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} };
let quiet = new Set(lsGet("kakao.quiet", []));
let notifyOn = lsGet("kakao.notify", false);
const isQuiet = name => quiet.has(name);
function toggleQuiet(name){
  if(!name) return;
  if(quiet.has(name)) quiet.delete(name); else quiet.add(name);
  lsSet("kakao.quiet", [...quiet]);
  prevRoomsKey=""; render();
  setStatus(quiet.has(name) ? (" 조용히: "+name) : (" 알림복귀: "+name));
}
function setStatus(s){ st.status = s; prevStatus=""; render(); }

// ---- mobile view switching (list <-> conversation) with browser history ----
function setView(v){
  view = v;
  document.body.dataset.view = v;
  $("back").setAttribute("aria-hidden", v==="convo" ? "false" : "true");
}
function enterConvo(){
  if(isMobile()){
    setView("convo");
    history.pushState({view:"convo"}, "");
  }
}
$("back").addEventListener("click", ()=>{
  if(history.state && history.state.view==="convo") history.back();
  else setView("list");
});
window.addEventListener("popstate", ()=>{ if(isMobile()) setView("list"); });

// ---- rendering: touch DOM only when the relevant slice actually changed ----
let prevRoomsKey="", prevSelKey="", prevMsgsKey="", prevStatus="", prevTitle="";

function roomRow(r,i){
  const div = document.createElement("div");
  div.className = "row" + (r.unread?" unread":"");
  div.setAttribute("role","option");
  div.dataset.room = r.name;
  const mc = r.members_count ? '<span class="mc">('+esc(r.members_count)+')</span>' : '';
  const mute = isQuiet(r.name) ? '<span class="mute" title="조용히">● </span>' : '';
  const tm = r.time ? '<span class="tm">'+esc(r.time)+'</span>' : '';
  const badge = r.unread ? '<span class="badge">'+r.unread+'</span>' : '';
  div.innerHTML =
    '<div class="r1"><span class="nm">'+mute+esc(r.name)+' '+mc+'</span>'+tm+'</div>' +
    '<div class="r2"><span class="pv">'+esc(r.last_message||'')+'</span>'+badge+'</div>';
  div.onclick = ()=>{ sel=i; openSel(); };
  return div;
}
function renderRooms(){
  const key = JSON.stringify(st.rooms.map(r=>[r.name,r.unread,r.time,r.last_message,r.members_count,isQuiet(r.name)]));
  if(key!==prevRoomsKey){
    prevRoomsKey = key;
    const list = $("list");
    list.innerHTML = "";
    st.rooms.forEach((r,i)=> list.appendChild(roomRow(r,i)));
    prevSelKey = "";              // force selection repaint
  }
  const selKey = sel+"/"+focus;
  if(selKey!==prevSelKey){
    prevSelKey = selKey;
    const list = $("list");
    [...list.children].forEach((el,i)=>{
      const on = (i===sel && focus==="list");
      el.classList.toggle("sel", on);
      el.setAttribute("aria-selected", on?"true":"false");
    });
    const selEl = list.children[sel];
    if(selEl && focus==="list") selEl.scrollIntoView({block:"nearest"});
  }
}

// stable 6-colour palette for sender names (WeeChat-style nick hashing)
const NICKCOL = ["#7aa2f7","#9ece6a","#e0af68","#bb9af7","#7dcfff","#f7768e"];
function nickColor(name){
  let h=0; for(const ch of (name||"")) h=(h*31 + ch.charCodeAt(0))>>>0;
  return NICKCOL[h % NICKCOL.length];
}
// msg + resolved display name (""=omit with ·). Returns a DOM node.
function msgNode(msg, rank, who, mine){
  if(msg.kind==="date"){
    const d=document.createElement("div"); d.className="daterow";
    d.textContent="──── "+(msg.text||"")+" ────"; return d;
  }
  const line=document.createElement("div");
  line.className="msg irc"+(mine?" me":"");
  const w=document.createElement("span"); w.className="who";
  w.textContent = who==="·" ? "·" : who;
  if(!mine && who && who!=="·") w.style.color = nickColor(who);
  const sep=document.createElement("span"); sep.className="sep"; sep.textContent="│";
  const body=document.createElement("span"); body.className="body";
  const kind=msg.kind||"text";
  if(kind==="photo"||kind==="video"||kind==="file"){
    const b=document.createElement("button"); b.className="dlbtn";
    const label = kind==="file" ? (msg.name||msg.text||"[File]") : msg.text;
    b.innerHTML='<span class="dlico">⤓</span> '+esc(label);
    b.title="탭해서 받기";
    b.onclick=()=>startExport(kind, rank, msg.name||"", b);
    body.appendChild(b);
  } else {
    body.textContent = msg.text||"";
  }
  line.appendChild(w); line.appendChild(sep); line.appendChild(body);
  return line;
}
function renderMessages(){
  const key = JSON.stringify(st.messages);
  if(key===prevMsgsKey) return;
  const m = $("msgs");
  // messages are the *visible* window on the device, so a new message shifts the
  // window rather than purely appending — follow the tail only if the reader was
  // already at the bottom; otherwise keep their position and flag new content.
  const firstLoad = (prevMsgsKey==="");
  const wasBottom = firstLoad || (m.scrollHeight - m.scrollTop - m.clientHeight) < 48;
  const keepTop = m.scrollTop;
  prevMsgsKey = key; prevMsgsLen = st.messages.length;
  if(!st.messages.length){ m.innerHTML = '<div class="empty">—</div>'; hideNew(); return; }
  // ranks per downloadable kind (stable across the whole list); resolve the
  // IRC display name per line (· = same sender as the line above / omitted)
  const ranks = {};
  let prevKey = null;
  const frag = document.createDocumentFragment();
  st.messages.forEach(msg=>{
    const kind=(msg&&msg.kind)||"text";
    if(kind==="date"){ prevKey=null; frag.appendChild(msgNode(msg)); return; }
    const mine = !!msg.is_mine;
    const key = mine ? "\u0000me" : (msg.sender || prevKey || "");
    let who;
    if(mine) who = (key===prevKey) ? "·" : "나";
    else who = (msg.sender && key!==prevKey) ? msg.sender : "·";
    prevKey = key;
    let rank=0; if(kind==="photo"||kind==="video"||kind==="file"){ rank=ranks[kind]||0; ranks[kind]=rank+1; }
    frag.appendChild(msgNode(msg, rank, who, mine));
  });
  m.replaceChildren(frag);   // note: this resets scrollTop to 0
  if(wasBottom){ m.scrollTop = m.scrollHeight; hideNew(); }
  else { m.scrollTop = keepTop; showNew(); }
}
let prevMsgsLen = 0;
function showNew(){ $("newmsg").classList.add("show"); }
function hideNew(){ $("newmsg").classList.remove("show"); }
$("newmsg").addEventListener("click", ()=>{ const m=$("msgs"); m.scrollTop=m.scrollHeight; hideNew(); });
$("msgs").addEventListener("scroll", ()=>{
  const m=$("msgs");
  if((m.scrollHeight - m.scrollTop - m.clientHeight) < 48) hideNew();
});

function render(){
  renderRooms();
  const title = st.room ? ("— "+st.room+" —") : "(select a room)";
  if(title!==prevTitle){ prevTitle=title; $("titletext").textContent = title; }
  renderMessages();
  $("inputline").className = "inputline" + (focus==="input" ? "" : " blur");
  $("loginbanner").hidden = !st.login_needed;
  const status = " " + (st.status||"");
  if(status!==prevStatus){ prevStatus=status; $("status").textContent = status; }
}

const draftKey = name => "kakao.draft:" + name;
function autogrow(){ input.style.height="auto"; input.style.height=Math.min(input.scrollHeight, window.innerHeight*0.4)+"px"; }
function saveDraft(){ if(st.room) lsSet(draftKey(st.room), input.value); }
function loadDraft(name){ input.value = lsGet(draftKey(name), "") || ""; autogrow(); }

function openSel(){
  const r = st.rooms[sel];
  if(!r) return;
  focus = "list";
  saveDraft();                      // keep the draft for the room we're leaving
  post("/api/open", {room:r.name});
  enterConvo();
  prevMsgsKey = "";                 // new room: force a message repaint
  loadDraft(r.name);
  if(!isMobile()){ focus="input"; input.focus(); }
  render();
}

// receive a media/file bubble: Save it off the device, then download in-browser
async function startExport(kind, rank, name, el){
  if(el.classList.contains("dl-busy")) return;
  el.classList.add("dl-busy");
  try{
    const r = await fetch("/api/export", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({kind, rank, name})});
    if(!r.ok){ el.classList.remove("dl-busy"); return; }
    const id = (await r.json()).id;
    for(let i=0;i<90;i++){
      await new Promise(res=>setTimeout(res, 1000));
      const s = await fetch("/api/export/"+id);
      if(!s.ok) continue;
      const js = await s.json();
      if(js.ready){ window.location = "/api/download/"+js.token; break; }
      if(js.error) break;
    }
  }catch(e){ /* status reflects worker result via poll */ }
  el.classList.remove("dl-busy");
}

async function post(path, body){
  try{
    await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body||{})});
  }catch(e){ /* transient */ }
}

let prevUnread = {};
function updateTitleAndNotify(){
  // tab title badge: total unread across non-quiet rooms
  let total = 0;
  for(const r of st.rooms){ if(!isQuiet(r.name)) total += (r.unread||0); }
  document.title = (total>0 ? "("+total+") " : "") + "kakao";
  // opt-in notification only when the tab is hidden and a non-quiet room grew
  if(notifyOn && document.hidden && "Notification" in window
     && Notification.permission==="granted"){
    for(const r of st.rooms){
      const before = prevUnread[r.name]||0;
      if(!isQuiet(r.name) && (r.unread||0) > before){
        try{ new Notification("kakao — "+r.name,
             {body:(r.last_message||"새 메시지"), tag:"kakao:"+r.name}); }catch(e){}
      }
    }
  }
  prevUnread = {}; for(const r of st.rooms) prevUnread[r.name] = r.unread||0;
}
async function toggleNotify(){
  if(!("Notification" in window)){ setStatus(" 이 브라우저는 알림 미지원"); return; }
  if(!notifyOn){
    let perm = Notification.permission;
    if(perm==="default") perm = await Notification.requestPermission();
    if(perm!=="granted"){ setStatus(" 알림 권한 거부됨"); return; }
    notifyOn = true;
  } else { notifyOn = false; }
  lsSet("kakao.notify", notifyOn);
  setStatus(notifyOn ? " 알림 켜짐(탭 숨김 시)" : " 알림 꺼짐");
}

async function poll(){
  try{
    const r = await fetch("/api/state?since="+st.seq);
    if(r.status===204) return;
    if(r.ok){ st = await r.json(); render(); updateTitleAndNotify(); }
  }catch(e){ /* keep trying */ }
}

function sendCurrent(){
  const t = input.value;
  if(t.trim()){
    post("/api/send", {text:t});
    input.value="";
    if(st.room) lsSet(draftKey(st.room), "");
    autogrow();
  }
}

// jump to next/prev room (optionally only unread), opening it
function jumpRoom(dir, unreadOnly){
  if(!st.rooms.length) return;
  let i = sel;
  for(let n=0;n<st.rooms.length;n++){
    i = (i + dir + st.rooms.length) % st.rooms.length;
    if(!unreadOnly || (st.rooms[i].unread||0) > 0){ sel=i; openSel(); return; }
  }
}

// keyboard — mirror the TUI key map (desktop); input stays usable on mobile
document.addEventListener("keydown", (e)=>{
  // overlays capture their own keys first
  if($("screenovl").classList.contains("show")){ if(e.key==="Escape") closeScreen(); return; }
  if($("switcher").classList.contains("show")){ switcherKey(e); return; }
  if($("helpovl").classList.contains("show")){ if(e.key==="Escape"||e.key==="?") closeOverlays(); return; }

  // other text fields (console path boxes) type normally; kbcap handles itself
  const t = e.target;
  if(t!==input && (t.tagName==="INPUT" || t.tagName==="TEXTAREA")) return;
  // console mode: only the mode/help keys — no chat navigation
  if(mode==="console"){
    if(e.ctrlKey||e.metaKey||e.altKey) return;
    if(e.key==="c"){ e.preventDefault(); setMode("chat"); }
    else if(e.key==="?"){ e.preventDefault(); openHelp(); }
    return;
  }
  // quick switcher / help work from anywhere
  if((e.key==="k"||e.key==="K") && (e.ctrlKey||e.metaKey)){ e.preventDefault(); openSwitcher(); return; }
  // room navigation with Alt works even while typing
  if(e.altKey && (e.key==="ArrowUp"||e.key==="ArrowDown")){
    e.preventDefault(); jumpRoom(e.key==="ArrowDown"?1:-1, e.shiftKey); return;
  }

  if(e.target===input){
    if(e.key==="Tab"){ e.preventDefault(); focus="list"; input.blur(); render(); }
    else if(e.key==="Escape"){ focus="list"; input.blur(); render(); if(isMobile()) $("back").click(); }
    else if(e.key==="Enter" && e.ctrlKey){ e.preventDefault();
      const s=input.selectionStart, v=input.value;
      input.value=v.slice(0,s)+"\n"+v.slice(input.selectionEnd);
      input.selectionStart=input.selectionEnd=s+1; autogrow(); }
    // ignore Enter while a Korean/IME syllable is still composing — otherwise the
    // send fires early and the final syllable lingers and gets sent again
    else if(e.key==="Enter" && !e.shiftKey && !e.isComposing && e.keyCode!==229){
      e.preventDefault(); sendCurrent(); }
    // Shift+Enter falls through to the textarea's default newline
    return;                       // single keys (r, /, ?) must not fire while typing
  }
  if(e.key==="/"){ e.preventDefault(); openSwitcher(); return; }
  if(e.key==="?"){ e.preventDefault(); openHelp(); return; }
  if(e.key==="Tab"){ e.preventDefault(); focus="input"; input.focus(); render(); }
  else if(e.key==="ArrowUp"){ e.preventDefault(); sel=Math.max(0,sel-1); render(); }
  else if(e.key==="ArrowDown"){ e.preventDefault(); sel=Math.min(st.rooms.length-1,sel+1); render(); }
  else if(e.key==="Enter"){ e.preventDefault(); openSel(); }
  else if(e.key==="r"){ post("/api/rooms"); }
  else if(e.key==="m"){ toggleQuiet(st.room); }
  else if(e.key==="n"){ toggleNotify(); }
  else if(e.key==="g"){ openScreen(); }
  else if(e.key==="c"){ e.preventDefault(); setMode("console"); }
});
input.addEventListener("focus", ()=>{ if(focus!=="input"){ focus="input"; render(); }});
input.addEventListener("input", autogrow);
$("send").addEventListener("click", ()=>{ sendCurrent(); input.focus(); });

// ---- quick switcher overlay (fuzzy room filter) ----
let qsel = 0, qlist = [];
function openSwitcher(){ $("switcher").classList.add("show"); $("qin").value=""; qsel=0; renderSwitcher(); $("qin").focus(); }
function openHelp(){ $("helptext").innerHTML = HELP; $("helpovl").classList.add("show"); }
function closeOverlays(){ $("switcher").classList.remove("show"); $("helpovl").classList.remove("show"); }
function fuzzy(q, name){
  q=q.toLowerCase(); name=name.toLowerCase(); let i=0;
  for(const ch of name){ if(ch===q[i]) i++; if(i>=q.length) return true; }
  return q.length===0;
}
function renderSwitcher(){
  const q = $("qin").value.trim();
  qlist = st.rooms.map((r,idx)=>({r,idx})).filter(o=> q==="" || fuzzy(q,o.r.name));
  if(qsel>=qlist.length) qsel=Math.max(0,qlist.length-1);
  const res = $("qres"); res.innerHTML="";
  qlist.forEach((o,j)=>{
    const d=document.createElement("div");
    d.className="row"+(j===qsel?" sel":"");
    d.setAttribute("role","option");
    const mc=o.r.members_count?' <span class="mc">('+esc(o.r.members_count)+')</span>':'';
    d.innerHTML='<span class="nm">'+esc(o.r.name)+mc+'</span>';
    d.onclick=()=>{ sel=o.idx; closeOverlays(); openSel(); };
    res.appendChild(d);
  });
}
$("qin").addEventListener("input", ()=>{ qsel=0; renderSwitcher(); });
function switcherKey(e){
  if(e.key==="Escape"){ e.preventDefault(); closeOverlays(); }
  else if(e.key==="ArrowDown"){ e.preventDefault(); qsel=Math.min(qlist.length-1,qsel+1); renderSwitcher(); }
  else if(e.key==="ArrowUp"){ e.preventDefault(); qsel=Math.max(0,qsel-1); renderSwitcher(); }
  else if(e.key==="Enter"){ e.preventDefault(); const o=qlist[qsel]; if(o){ sel=o.idx; closeOverlays(); openSel(); } }
}

// file attach: + opens the picker, selection uploads to the open room
$("attach").addEventListener("click", ()=>{
  if(!st.room){ st.status = " 먼저 방을 선택하세요"; prevStatus=""; render(); return; }
  $("file").click();
});
async function uploadFile(f){
  if(!f || !st.room) return;
  const name = f.name || ("paste-"+Date.now()+(f.type==="image/png"?".png":""));
  setStatus(" attaching " + name + " …");
  try{
    await fetch("/api/sendfile?name=" + encodeURIComponent(name), {method:"POST", body:f});
  }catch(err){ /* status via poll */ }
}
$("file").addEventListener("change", async (e)=>{ await uploadFile(e.target.files[0]); e.target.value=""; });

// paste an image into the input, or drag-and-drop a file onto the conversation
input.addEventListener("paste", (e)=>{
  const items = e.clipboardData && e.clipboardData.items;
  if(!items) return;
  for(const it of items){
    if(it.kind==="file"){ const f=it.getAsFile(); if(f){ e.preventDefault(); uploadFile(f); return; } }
  }
});
const pane = document.querySelector(".pane");
["dragenter","dragover"].forEach(ev=> pane.addEventListener(ev, e=>{ e.preventDefault(); $("inputline").classList.add("drop"); }));
["dragleave","drop"].forEach(ev=> pane.addEventListener(ev, e=>{ e.preventDefault(); $("inputline").classList.remove("drop"); }));
pane.addEventListener("drop", e=>{ const f=e.dataTransfer && e.dataTransfer.files[0]; if(f) uploadFile(f); });

// ---- live device screen view/control (overlay for re-login + console center) ----
// One <img id=scr> and one frame loop; the element is re-parented between the
// overlay and the console so frames are never fetched twice.
let frameLoop=false, fpsCount=0, fpsT=Date.now(), scrSeq=0;
const screenWanted = () => $("screenovl").classList.contains("show") || mode==="console";
function startFrames(){ if(!frameLoop){ frameLoop=true; scrSeq=0; pollFrame(); } }
function openScreen(){
  if(mode==="console"){ $("kbcap").focus({preventScroll:true}); return; }
  document.querySelector("#screenovl .scrwrap").appendChild($("scr"));
  $("screenovl").classList.add("show"); startFrames();
}
function closeScreen(){ $("screenovl").classList.remove("show"); }
// poll the tiny /info (keeps the viewer alive) and only fetch a new frame when the
// capture seq changed — avoids re-downloading identical frames (mobile bandwidth)
function pollFrame(){
  if(!screenWanted()){ frameLoop=false; return; }
  fetch("/api/screen/info").then(r=>r.json()).then(j=>{
    if(!screenWanted()){ frameLoop=false; return; }
    if(j.seq && j.seq !== scrSeq){
      scrSeq = j.seq;
      const nx = new Image();
      nx.onload = ()=>{
        if(!screenWanted()) return;
        $("scr").src = nx.src;
        fpsCount++; const now=Date.now();
        if(now-fpsT >= 1000){ $("fps").textContent = $("cfps").textContent = fpsCount+" fps";
          fpsCount=0; fpsT=now; }
      };
      nx.src = "/api/screen/frame?seq=" + j.seq;
    }
    setTimeout(pollFrame, 250);
  }).catch(()=>{ if(screenWanted()) setTimeout(pollFrame, 600); else frameLoop=false; });
}
async function scrPost(path, body){
  try{ await fetch("/api/screen/"+path, {method:"POST",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}); }catch(e){}
}
const scr = $("scr");
let pd = null;
scr.addEventListener("pointerdown", e=>{ e.preventDefault(); pd={x:e.offsetX,y:e.offsetY,t:Date.now()};
  try{ scr.setPointerCapture(e.pointerId); }catch(_){}
  if(mode==="console") $("kbcap").focus({preventScroll:true}); });  // keyboard → device
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
$("scrbtn").onclick = openScreen;
$("scrbtn2").onclick = openScreen;
$("loginopen").onclick = openScreen;
$("scrclose").onclick = closeScreen;
$("kbback").onclick = ()=> scrPost("key",{name:"back"});
$("kbhome").onclick = ()=> scrPost("key",{name:"home"});
$("kbenter").onclick = ()=> scrPost("key",{name:"enter"});
$("scrsend").onclick = ()=>{ const t=$("scrtext").value; if(t){ scrPost("text",{text:t}); $("scrtext").value=""; } };
$("scrtext").addEventListener("keydown", e=>{ if(e.key==="Enter" && !e.isComposing && e.keyCode!==229){ e.preventDefault(); $("scrsend").click(); } e.stopPropagation(); });
document.querySelectorAll(".ovl").forEach(o=> o.addEventListener("click", e=>{ if(e.target===o){ if(o.id==="screenovl") closeScreen(); else closeOverlays(); } }));

// ================= console mode: device screen + file managers =================
let consoleOK = true;
function setMode(m, save=true){
  if(m==="console" && !consoleOK){ setStatus(" 콘솔 비활성 — 비로컬 바인드는 --allow-screen 필요"); return; }
  mode = m;
  document.body.dataset.mode = m;
  $("m-chat").classList.toggle("on", m==="chat");
  $("m-console").classList.toggle("on", m==="console");
  $("m-chat").setAttribute("aria-selected", m==="chat"); $("m-console").setAttribute("aria-selected", m==="console");
  $("mconsole").textContent = m==="console" ? "💬 채팅" : "⌨ 콘솔";
  if(save) lsSet("kakao.mode", m);
  history.replaceState(history.state, "", m==="console" ? "#console" : location.pathname);
  if(m==="console"){
    closeScreen();
    $("conwrap").appendChild($("scr"));
    startFrames();
    if(!fp.android.loaded) loadPane("android", fp.android.path);
    if(!fp.local.loaded) loadPane("local", fp.local.path);
    setStatus(" 콘솔 — 열려 있는 동안 채팅 갱신은 멈춥니다");
  }
}
$("m-chat").onclick = ()=> setMode("chat");
$("m-console").onclick = ()=> setMode("console");
$("mconsole").onclick = ()=> setMode(mode==="console" ? "chat" : "console");
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

// initial mode: #console deep-link or last used; console needs the screen API
fetch("/api/screen/info").then(r=>r.json()).then(j=>{
  consoleOK = !!j.enabled;
  if(!consoleOK){
    $("m-console").disabled = true; $("mconsole").disabled = true;
    $("m-console").title = "비활성 — 비로컬 바인드는 --allow-screen 필요";
  }
  const want = location.hash==="#console" ? "console" : lsGet("kakao.mode", "chat");
  if(want==="console" && consoleOK) setMode("console", false);
}).catch(()=>{});

// keep the input line visible when the mobile keyboard opens (visualViewport)
function fitViewport(){
  const vv = window.visualViewport;
  if(vv) document.body.style.height = vv.height + "px";
}
if(window.visualViewport){
  window.visualViewport.addEventListener("resize", fitViewport);
  window.visualViewport.addEventListener("scroll", fitViewport);
  fitViewport();
}

poll();
setInterval(poll, 1000);
</script>
</body></html>
"""

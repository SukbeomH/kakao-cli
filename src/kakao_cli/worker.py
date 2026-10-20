"""Background worker that owns the Driver.

A single thread serializes every device access: it polls the open room and
handles send / switch-room / refresh commands between polls. UIs (curses TUI,
web UI) only push commands in and drain events out — they never touch the
Driver directly, so there is never more than one adb operation in flight.
"""
from __future__ import annotations
import queue
import threading


class Worker(threading.Thread):
    """Owns the Driver. Commands in, events out — one device op at a time.

    Commands (via ``put``):   ("rooms",) ("open", name) ("send", text)
                              ("sendfile", path) ("export", id, kind, rank, name)
                              ("quit",)
    Events (on ``events``):    ("rooms", list) ("status", str) ("opened", name)
                               ("messages", (room, detailed)) ("export_result", dict)
                               ("export_error", dict)
    """

    def __init__(self, dev, poll_interval=2.5, export_dir=None, screen=None):
        super().__init__(daemon=True)
        from .driver import Driver
        self.d = Driver(dev)
        self.cmds: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self.room: str | None = None
        self.poll_interval = poll_interval
        self.export_dir = export_dir
        self.screen = screen            # ScreenBuffer or None (live device view)
        self.last_detailed: list = []
        self.login_needed = False
        self._last_login_check = 0.0
        self._stop = False

    def put(self, *cmd):
        self.cmds.put(cmd)

    def _emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        try:
            self._refresh_rooms()
        except Exception as e:
            if not self._maybe_login():
                self._emit("status", f"rooms load failed: {e} (press r to retry)")
        while not self._stop:
            # live screen view: capture frames fast and keep controls responsive;
            # pause the periodic chat poll so there's no adb contention.
            if self.screen is not None and self.screen.active():
                self._capture()
                try:
                    cmd = self.cmds.get(timeout=0.25)
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    try:
                        self._handle(cmd)
                    except Exception as e:
                        self._emit("status", f"err: {e}")
                continue
            try:
                cmd = self.cmds.get(timeout=self.poll_interval)
            except queue.Empty:
                cmd = ("poll",)
            try:
                self._handle(cmd)
            except Exception as e:  # keep the UI alive on transient adb errors
                self._emit("status", f"err: {e}")

    def _handle(self, cmd):
        op = cmd[0]
        if op == "quit":
            self._stop = True
        elif op == "rooms":
            self._refresh_rooms()
        elif op == "open":
            self.room = cmd[1]
            self._emit("status", f"opening {self.room} …")
            self.d._open_target(self.room)
            self._emit("opened", self.room)
            self._emit("status", f"in {self.room}")
            self._poll_messages()
        elif op == "send":
            if self.room and cmd[1].strip():
                self._emit("status", "sending …")
                res = self.d.send_in_room(cmd[1])
                self._emit("status", f"sent ({res})")
                self._poll_messages()
        elif op == "sendfile":
            if self.room:
                self._send_file(cmd[1])
        elif op == "export":
            self._export(*cmd[1:])
        elif op == "tap":
            self.d.dev.tap(cmd[1], cmd[2])
        elif op == "swipe":
            self.d.dev.swipe(*cmd[1:])
        elif op == "key":
            if isinstance(cmd[1], int):              # console keyboard passthrough
                self.d.dev.keyevent(cmd[1])
            else:
                code = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME",
                        "enter": "KEYCODE_ENTER"}.get(cmd[1])
                if code:
                    self.d.dev.keyevent(code)
        elif op == "screentext":
            self.d.dev.type_text(cmd[1])
        elif op == "poll":
            if self.room:
                self._poll_messages()

    def _send_file(self, path):
        """Send an uploaded file into the open room, then re-anchor and poll.

        Reuses Driver.send_file (image/video via the share picker, documents via
        the in-room '+' → File path). The share-intent branch can leave the app
        off the room, so re-open the target if the input box is gone before
        polling. The temp upload is always removed."""
        import os
        base = os.path.basename(path)
        self._emit("status", f"attaching {base} …")
        try:
            res = self.d.send_file(self.room, path)
            self._emit("status", f"sent ({res})")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        from . import RID
        if self.d.screen().first(rid=RID["input"]) is None:
            self.d._open_target(self.room)
        self._poll_messages()

    def _export(self, xid, kind, rank, name):
        """Save a visible media/file bubble off the device and pull it locally.
        Matches the requested bubble in a fresh dump (files by name, else by rank
        among same-kind), taps → Save → diffs the KakaoTalk save dirs → pulls."""
        import os, time
        if not self.export_dir:
            self._emit("export_error", {"id": xid, "error": "exports disabled"})
            return
        if not self.room:
            self._emit("export_error", {"id": xid, "error": "no room open"})
            return
        try:
            self._emit("status", "receiving …")
            detailed = self.d._messages_detailed(self.d.screen())
            same = [m for m in detailed if m.get("kind") == kind and m.get("bounds")]
            target = None
            if kind == "file" and name:
                target = next((m for m in same if m.get("name") == name), None)
            if target is None and isinstance(rank, int) and 0 <= rank < len(same):
                target = same[rank]
            if target is None:
                self._emit("export_error", {"id": xid, "error": "message no longer visible"})
                return
            devpath = self.d.export_visible(target["bounds"])
            base = name or os.path.basename(devpath)
            local = os.path.join(self.export_dir, "%d_%s" % (int(time.time() * 1000), base))
            self.d.dev.pull(devpath, local)
            self._emit("export_result", {"id": xid, "path": local, "name": base,
                                         "mime": _mime_for(base)})
            self._emit("status", f"received {base}")
        except Exception as e:
            self._emit("export_error", {"id": xid, "error": str(e)})
        finally:
            # export navigates through a viewer; re-anchor and refresh the room
            if self.room:
                try:
                    self.d._open_target(self.room)
                    self._poll_messages()
                except Exception:
                    pass

    def _capture(self):
        try:
            self.screen.set_frame(self.d.dev.screencap_bytes())
        except Exception as e:
            self._emit("status", f"screen capture err: {e}")
        self._maybe_login()

    def _maybe_login(self) -> bool:
        """Throttled login/onboarding detection. Emits a 'login' event on change
        and, when login completes, reloads the room list. Returns current state."""
        import time
        from .screen import is_login_screen
        now = time.time()
        if now - self._last_login_check < 2.0:
            return self.login_needed
        self._last_login_check = now
        try:
            act = self.d.activity() or ""
        except Exception:
            return self.login_needed
        login = is_login_screen(act)
        if login != self.login_needed:
            self.login_needed = login
            self._emit("login", login)
            if login:
                self._emit("status", "로그인이 필요합니다 — 화면 열기")
            else:
                try:
                    self._refresh_rooms()   # login finished → load rooms
                except Exception:
                    pass
        return login

    def _refresh_rooms(self):
        self._emit("status", "loading rooms …")
        rooms = self.d.list_rooms()
        self._emit("rooms", rooms)
        self._emit("status", f"{len(rooms)} rooms")

    def _poll_messages(self):
        self.last_detailed = self.d._messages_detailed(self.d.screen())
        self._emit("messages", (self.room, self.last_detailed))


_MIMES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".m4v": "video/x-m4v", ".3gp": "video/3gpp",
    ".pdf": "application/pdf", ".zip": "application/zip", ".txt": "text/plain",
    ".doc": "application/msword", ".hwp": "application/x-hwp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel", ".ppt": "application/vnd.ms-powerpoint",
}


def _mime_for(name: str) -> str:
    import os
    return _MIMES.get(os.path.splitext(name or "")[1].lower(), "application/octet-stream")

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
                              ("sendfile", path) ("quit",)
    Events (on ``events``):    ("rooms", list) ("status", str) ("opened", name)
                               ("messages", (room, list))
    """

    def __init__(self, dev, poll_interval=2.5):
        super().__init__(daemon=True)
        from .driver import Driver
        self.d = Driver(dev)
        self.cmds: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self.room: str | None = None
        self.poll_interval = poll_interval
        self._stop = False

    def put(self, *cmd):
        self.cmds.put(cmd)

    def _emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        try:
            self._refresh_rooms()
        except Exception as e:
            self._emit("status", f"rooms load failed: {e} (press r to retry)")
        while not self._stop:
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

    def _refresh_rooms(self):
        self._emit("status", "loading rooms …")
        rooms = self.d.list_rooms()
        self._emit("rooms", rooms)
        self._emit("status", f"{len(rooms)} rooms")

    def _poll_messages(self):
        msgs = self.d._messages_on_screen(self.d.screen())
        self._emit("messages", (self.room, msgs))

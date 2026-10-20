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
                              ("tap", x, y) ("swipe", …) ("key", name|code)
                              ("screentext", text) ("quit",)
    Events (on ``events``):    ("rooms", list) ("status", str) ("opened", name)
                               ("messages", (room, detailed)) ("login", bool)

    chat=False (the web console) skips the startup room-list load and the login
    watcher — it only runs device input / PNG capture for the console.
    """

    def __init__(self, dev, poll_interval=2.5, screen=None, chat=True):
        super().__init__(daemon=True)
        from .driver import Driver
        self.d = Driver(dev)
        self.cmds: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self.room: str | None = None
        self.poll_interval = poll_interval
        self.screen = screen            # ScreenBuffer or None (live device view)
        self.chat = chat
        self.login_needed = False
        self._last_login_check = 0.0
        self._stop = False

    def put(self, *cmd):
        self.cmds.put(cmd)

    def _emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        if self.chat:
            try:
                self._refresh_rooms()
            except Exception as e:
                if not self._maybe_login():
                    self._emit("status", f"rooms load failed: {e} (press r to retry)")
        else:
            self._emit("status", "ready")
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

    def _capture(self):
        try:
            self.screen.set_frame(self.d.dev.screencap_bytes())
        except Exception as e:
            self._emit("status", f"screen capture err: {e}")

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
        self._emit("messages", (self.room, self.d._messages_detailed(self.d.screen())))

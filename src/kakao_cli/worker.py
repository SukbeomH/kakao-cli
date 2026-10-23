"""KakaoTalk chat worker built on the shared Android input worker."""
from __future__ import annotations
from android_console.worker import Worker as ConsoleWorker


class Worker(ConsoleWorker):
    def __init__(self, dev, poll_interval=2.5, screen=None, chat=True):
        super().__init__(dev, poll_interval=poll_interval, screen=screen)
        from .driver import Driver
        self.d = Driver(dev)
        self.receiver = None
        self.room = None
        self.chat = chat
        self.login_needed = False
        self._last_login_check = 0.0

    def _startup(self):
        if not self.chat:
            return super()._startup()
        try:
            self._refresh_rooms()
        except Exception as e:
            if not self._maybe_login():
                self._emit("status", f"rooms load failed: {e} (press r to retry)")

    def _handle(self, cmd):
        op = cmd[0]
        if op == "rooms":
            self._refresh_rooms()
        elif op == "open":
            self.room = cmd[1]
            self._emit("status", f"opening {self.room} …")
            if self.receiver is None:
                from .receiver import Receiver
                self.receiver = Receiver(self.d.dev, warning=lambda text: self._emit("status", text))
            # DB 선택은 화면을 열지 않는다.
            if not self.receiver.db:
                from . import consolelock
                consolelock.check(self.d.dev)
                self.d._open_target(self.room)
            self._emit("opened", self.room)
            self._emit("status", f"in {self.room}")
            self._poll_messages()
        elif op == "send":
            if self.room and cmd[1].strip():
                self._emit("status", "sending …")
                from . import consolelock
                consolelock.check(self.d.dev)
                target = self.room
                if target.startswith("room:") and self.receiver and self.receiver.db:
                    selected = self.receiver.db.resolve(target)
                    target = self.receiver.db.room_name(selected)
                    if target.startswith("room:"):
                        from .errors import KakaoError
                        raise KakaoError("방 이름을 확인할 수 없습니다. 웹 콘솔에서 직접 선택하세요")
                    if self.receiver.db.resolve(target)['id'] != selected['id']:
                        from .errors import KakaoError
                        raise KakaoError("방 이름과 ID가 일치하지 않습니다. 웹 콘솔에서 직접 선택하세요")
                self.d._open_target(target)
                res = self.d.send_in_room(cmd[1])
                self._emit("status", f"sent ({res})")
                self._poll_messages()
        elif op == "poll":
            if self.room:
                self._poll_messages()
        else:
            super()._handle(cmd)

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
        if self.receiver is None:
            from .receiver import Receiver
            self.receiver = Receiver(self.d.dev, warning=lambda text: self._emit("status", text))
        rooms = self.receiver.list_rooms()
        self._emit("rooms", rooms)
        self._emit("status", f"{len(rooms)} rooms")

    def _poll_messages(self):
        self._emit("messages", (self.room, self.receiver.read_detailed(self.room)))

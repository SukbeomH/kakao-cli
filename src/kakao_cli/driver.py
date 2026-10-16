"""High-level KakaoTalk automation on top of adb + the UI engine.

Robust navigation: dump -> find by resource-id/content-desc -> tap -> verify, with
retries. Resource-ids are stable/unobfuscated; coordinates are derived per-dump.
"""
from __future__ import annotations
import time

from .adb import Device
from .ui import Screen, Node
from .errors import KakaoError, SendUncertain
from . import RID


class Driver:
    def __init__(self, dev: Device):
        self.dev = dev

    def screen(self) -> Screen:
        return Screen(self.dev.dump())

    def _screen_where(self, pred, timeout=15.0, interval=0.6) -> Screen | None:
        end = time.time() + timeout
        while time.time() < end:
            s = self.screen()
            if pred(s):
                return s
            time.sleep(interval)
        return None

    def _find(self, finder, timeout=15.0, interval=0.6) -> Node | None:
        end = time.time() + timeout
        while time.time() < end:
            n = finder(self.screen())
            if n:
                return n
            time.sleep(interval)
        return None

    def tap_node(self, node: Node):
        x, y = node.center
        self.dev.tap(x, y)

    def activity(self):
        return self.dev.current_activity()

    # --- flows ---
    def open_chats_tab(self):
        n = self._find(lambda s: _desc(s, "Chats tab"), timeout=10)
        if not n:
            raise KakaoError("Chats tab not found", activity=self.activity())
        self.tap_node(n)
        time.sleep(1.0)

    def list_rooms(self) -> list[dict]:
        self.open_chats_tab()
        s = self._screen_where(lambda s: s.first(rid=RID["room_list"]) is not None, timeout=10)
        if not s:
            raise KakaoError("room list not found")
        rooms = []
        for name in s.find(rid=RID["room_name"]):
            y = name.bounds[1]
            msg = _nearest(s.find(rid=RID["room_last_message"]), y)
            tm = _nearest(s.find(rid=RID["room_time"]), y)
            rooms.append({"name": name.text,
                          "last_message": msg.text if msg else "",
                          "time": tm.text if tm else ""})
        return rooms

    def open_room(self, name: str):
        self.open_chats_tab()
        n = self._find(lambda s: s.first(rid=RID["room_name"], text=name), timeout=10)
        if not n:
            raise KakaoError(f"room not found: {name}")
        self.tap_node(n)
        if not self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=12):
            raise KakaoError("chat room did not open", activity=self.activity())

    def open_self_chat(self):
        fn = self._find(lambda s: _desc(s, "Friends tab"), timeout=10)
        if fn:
            self.tap_node(fn); time.sleep(0.8)
        av = self._find(lambda s: _desc_contains(s, "Profile photo"), timeout=8)
        if not av:
            raise KakaoError("profile avatar not found")
        self.tap_node(av); time.sleep(1.2)
        mc = self._find(lambda s: _desc(s, "My Chatroom") or s.first(text="My Chatroom"), timeout=8)
        if not mc:
            raise KakaoError("'My Chatroom' button not found")
        self.tap_node(mc)
        if not self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=12):
            raise KakaoError("self chat did not open", activity=self.activity())

    def send_in_room(self, text: str) -> str:
        s = self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=10)
        if not s:
            raise KakaoError("not in a chat room")
        self.tap_node(s.first(rid=RID["input"])); time.sleep(0.4)
        self.dev.type_text(text); time.sleep(0.6)
        snd = self.screen().first(rid=RID["send"])
        if not snd:
            raise KakaoError("send button not found")
        self.tap_node(snd); time.sleep(1.2)
        if not _poll(lambda: text in self.dev.dump(), timeout=6):
            raise SendUncertain("sent but could not confirm via readback", text=text)
        return "verified"

    def read_room(self, name, limit=20) -> list[str]:
        if name:
            self.open_room(name)
        s = self._screen_where(lambda s: s.first(rid=RID["chat_log"]) is not None, timeout=10)
        if not s:
            raise KakaoError("chat log not found")
        msgs = [n.text for n in s.find(rid=RID["room_last_message"]) if n.text]
        return msgs[-limit:]


def _desc(s: Screen, desc: str):
    for n in s.all():
        if n.desc == desc:
            return n
    return None


def _desc_contains(s: Screen, sub: str):
    for n in s.all():
        if sub in n.desc:
            return n
    return None


def _nearest(nodes, y):
    best, bd = None, 1 << 30
    for n in nodes:
        d = abs(n.bounds[1] - y)
        if d < bd:
            best, bd = n, d
    return best if bd < 120 else None


def _poll(pred, timeout=6.0, interval=0.6) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False

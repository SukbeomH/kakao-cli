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

    def ensure_main(self, timeout=12.0):
        """Make sure KakaoTalk is foreground on MainActivity (launch if needed).
        Recovers from a foreign foreground (system file picker, dialog) and, as a
        last resort, force-stops and relaunches the app."""
        import time as _t

        def _at_main():
            a = self.activity()
            return bool(a and a.endswith("main.MainActivity"))

        if _at_main():
            return
        # dismiss any non-KakaoTalk foreground (e.g. DocumentsUI picker) or dialog
        for _ in range(4):
            fg = self.dev.shell("dumpsys activity activities | grep -m1 topResumedActivity")
            if "com.kakao.talk" in fg:
                break
            self.dev.back(); _t.sleep(0.6)
        for restart in (False, True):
            if restart:
                self.dev.shell("am force-stop com.kakao.talk"); _t.sleep(1.5)
            self.dev.shell("am start -n com.kakao.talk/.activity.SplashActivity")
            end = _t.time() + timeout
            while _t.time() < end:
                if _at_main():
                    _t.sleep(0.8)
                    return
                _t.sleep(0.6)
        # give up silently; caller's _find will surface a precise error

    def open_chats_tab(self):
        self.ensure_main()
        n = self._find(lambda s: _desc_prefix(s, "Chats tab"), timeout=10)
        if not n:
            raise KakaoError("Chats tab not found", activity=self.activity())
        self.tap_node(n)
        time.sleep(1.0)

    def _read_room_list(self, s) -> list[dict]:
        rooms = []
        for r in s.list_items("recycler_view"):
            if "name" not in r:
                continue
            rooms.append({
                "name": r.get("name", ""),
                "last_message": r.get("message", ""),
                "time": r.get("time", ""),
                "members_count": r.get("members_count", ""),
                "unread": int(r["unread_count"]) if r.get("unread_count", "").isdigit() else 0,
            })
        return rooms

    def list_rooms(self) -> list[dict]:
        self.open_chats_tab()
        s = self._screen_where(lambda s: s.first(rid=RID["room_list"]) is not None, timeout=10)
        if not s:
            raise KakaoError("room list not found")
        return self._read_room_list(s)

    def track(self, interval: float = 5.0, window: int = 10):
        """Poll the chat list; emit each room's newest message when it changes.
        No notification/background dependency; catches muted chats too. Only the
        latest message per room per poll is seen (bursts between polls collapse)."""
        import time as _t
        self.open_chats_tab()
        seen: dict = {}      # room -> (last_message, time)
        recent: list = []    # rolling window of recent emitted events
        primed = False
        while True:
            try:
                s = self.screen()
                for r in self._read_room_list(s):
                    key = r["name"]
                    sig = (r["last_message"], r["time"])
                    if seen.get(key) != sig and r["last_message"]:
                        seen[key] = sig
                        if primed:
                            ev = {"room": r["name"], "text": r["last_message"],
                                  "time": r["time"], "unread": r["unread"]}
                            recent.append(ev)
                            del recent[:-window]
                            yield ev
                primed = True
            except Exception as e:
                yield {"_error": str(e)}
            _t.sleep(interval)

    def open_room(self, name: str):
        self.open_chats_tab()
        n = self._find(lambda s: s.first(rid=RID["room_name"], text=name), timeout=10)
        if not n:
            raise KakaoError(f"room not found: {name}")
        self.tap_node(n)
        if not self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=12):
            raise KakaoError("chat room did not open", activity=self.activity())

    def open_self_chat(self):
        last = None
        for attempt in range(3):
            try:
                self.ensure_main()
                fn = self._find(lambda s: _desc_prefix(s, "Friends tab"), timeout=10)
                if fn:
                    self.tap_node(fn); time.sleep(1.0)
                av = self._find(lambda s: _desc_contains(s, "Profile photo"), timeout=10)
                if not av:
                    last = "profile avatar not found"; continue
                self.tap_node(av); time.sleep(1.4)
                mc = self._find(lambda s: _desc(s, "My Chatroom") or s.first(text="My Chatroom"), timeout=8)
                if not mc:
                    last = "'My Chatroom' button not found"
                    self.dev.back(); time.sleep(0.8); continue
                self.tap_node(mc)
                if self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=12):
                    return
                last = "self chat did not open"
            except Exception as e:  # noqa
                last = str(e)
            time.sleep(1.0)
        raise KakaoError(f"open_self_chat failed: {last}", activity=self.activity())

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


    SHARE_COMPONENT = "com.kakao.talk/.activity.RecentExcludeIntentFilterActivity"

    def send_file(self, target: str, local_path: str) -> str:
        """Send an image/video to a room (by name) or self ('me') via ACTION_SEND.
        Attaches the exact file (content URI) — no gallery permission needed.
        """
        import os, time
        ext = os.path.splitext(local_path)[1].lower()
        DOC_MIME = {".pdf": "application/pdf", ".zip": "application/zip",
                    ".doc": "application/msword", ".txt": "text/plain",
                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ".xls": "application/vnd.ms-excel", ".ppt": "application/vnd.ms-powerpoint",
                    ".hwp": "application/x-hwp"}
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            kind, mime, folder = "image", "image/*", "Pictures"
        elif ext in (".mp4", ".mov", ".mkv", ".webm", ".3gp", ".m4v"):
            kind, mime, folder = "video", "video/*", "Movies"
        else:
            return self.send_doc(target, local_path)
        remote = f"/sdcard/{folder}/kc_{int(time.time())}{ext}"
        self.dev.push(local_path, remote)
        uri = self.dev.media_content_uri(remote, kind)
        if not uri:
            raise KakaoError("could not register file in MediaStore", remote=remote)
        self.dev.send_intent(uri, mime, self.SHARE_COMPONENT)
        # recipient picker
        want = "me" if target.lower() in ("me", "self", "나", "나와의 채팅") else target
        def finder(s):
            for n in s.all():
                if n.rid.endswith("chat_room_list_item"):
                    d = n.desc
                    if want == "me" and (", me," in d or d.startswith("not selected, me")):
                        return n
                    if want != "me" and want in d:
                        return n
            return None
        row = self._find(finder, timeout=12)
        if not row:
            raise KakaoError(f"recipient not found in share picker: {target}")
        if "not selected" in row.desc:
            self.tap_node(row); time.sleep(0.6)
        sub = self._find(lambda s: next((n for n in s.all()
                         if n.rid.endswith("submit_button") and n.text.strip()), None), timeout=8)
        if not sub:
            raise KakaoError("Send button not found in picker")
        self.tap_node(sub); time.sleep(2.0)
        return "sent"

    def _messages_on_screen(self, s) -> list[str]:
        """Ordered messages visible in the open chat room; media -> [Photo]/[Video]."""
        out = []
        rows = s.list_items("chat_log_recycler_list")
        for row in rows:
            txt = ""
            for k in ("message", "text", "text_with_pin"):
                if row.get(k, "").strip():
                    txt = row[k].strip(); break
            if not txt:
                media = row.get("image", "") or row.get("video", "")
                if media.strip():
                    txt = f"[{media.strip()}]"
            if txt:
                out.append(txt)
        if not out:  # fallback flat scan (text only)
            for n in s.all():
                if n.rid.split("/")[-1] in ("message", "text", "text_with_pin") and n.text.strip():
                    out.append(n.text.strip())
        return out

    def _open_target(self, name):
        if name and name.lower() in ("me", "self", "나", "나와의 채팅"):
            self.open_self_chat()
        elif name:
            self.open_room(name)

    def read_room(self, name, limit=20) -> list[str]:
        self._open_target(name)
        s = self._screen_where(lambda s: s.first(rid=RID["chat_log"]) is not None, timeout=10)
        if not s:
            raise KakaoError("chat log not found")
        return self._messages_on_screen(s)[-limit:]

    def poll_room(self, name, interval: float = 3.0, limit: int = 10):
        """Open ONE room and poll its visible messages; emit newly-appended messages
        as they arrive. Reliable (single room, foreground), no notification/background
        dependency. The room stays focused (incoming messages are marked read)."""
        import time as _t
        self._open_target(name)
        if not self._screen_where(lambda s: s.first(rid=RID["chat_log"]) is not None, timeout=10):
            raise KakaoError("chat log not found", room=name)
        prev_bottom = None
        primed = False
        while True:
            try:
                cur = self._messages_on_screen(self.screen())[-limit:]
                if cur:
                    if not primed:
                        prev_bottom = cur[-1]; primed = True
                    else:
                        # new = messages appended after the previous bottom
                        if prev_bottom in cur:
                            idx = len(cur) - 1 - cur[::-1].index(prev_bottom)
                            new = cur[idx + 1:]
                        else:
                            new = cur  # view refreshed/scrolled
                        for m in new:
                            yield {"room": name, "text": m}
                        if new:
                            prev_bottom = cur[-1]
            except Exception as e:
                yield {"_error": str(e)}
            _t.sleep(interval)


    def send_doc(self, target, local_path) -> str:
        """Send an arbitrary document (pdf/zip/doc/...) to a room via the in-room
        '+' -> File -> Select from File -> system file picker. Returns 'sent'."""
        import os, time
        name = f"kcdoc_{int(time.time())}_{os.path.basename(local_path)}"
        remote = f"/sdcard/Download/{name}"
        self.dev.push(local_path, remote)
        self.dev.shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{remote}")
        self._open_target(target)
        if not self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=10):
            raise KakaoError("chat room not open", room=target)
        # open the '+' media sheet
        plus = self.screen().first(rid="com.kakao.talk:id/media_send_layout")
        if not plus:
            raise KakaoError("'+' button not found")
        self.tap_node(plus); time.sleep(1.5)
        # scroll the sheet to reveal 'File', then tap it
        fnode = None
        for _ in range(4):
            fnode = next((n for n in self.screen().all() if n.desc == "File" or n.text == "File"), None)
            if fnode:
                break
            self.dev.shell("input swipe 800 2300 800 1400 400"); time.sleep(0.8)
        if not fnode:
            raise KakaoError("'File' option not found in + sheet")
        self.tap_node(fnode); time.sleep(1.5)
        # 'Select from File' -> system picker
        sel = self._find(lambda s: s.first(text="Select from File"), timeout=6)
        if not sel:
            raise KakaoError("'Select from File' not found")
        self.tap_node(sel); time.sleep(2.0)
        # DocumentsUI: find our file by name (search for robustness)
        srch = self._find(lambda s: next((n for n in s.all()
                        if n.rid.endswith("option_menu_search")), None), timeout=8)
        if srch:
            self.tap_node(srch); time.sleep(1.0)
            self.dev.type_text(name); time.sleep(1.5)
        item = self._find(lambda s: next((n for n in s.all() if name in n.desc or name in n.text), None), timeout=10)
        if not item:
            self.dev.back(); time.sleep(0.5); self.dev.back()  # dismiss picker
            raise KakaoError("file not found in picker", file=name)
        self.tap_node(item); time.sleep(2.5)
        # readback: a File bubble for our doc
        if not _poll(lambda: name in self.dev.dump(), timeout=8):
            raise SendUncertain("document sent but not confirmed via readback", file=name)
        return "sent"


def _desc_prefix(s: Screen, prefix: str):
    for n in s.all():
        if n.desc.startswith(prefix):
            return n
    return None




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


"""High-level KakaoTalk automation on top of adb + the UI engine.

Robust navigation: dump -> find by resource-id/content-desc -> tap -> verify, with
retries. Resource-ids are stable/unobfuscated; coordinates are derived per-dump.
"""
from __future__ import annotations
import time

from .adb import Device
from .ui import Screen, Node, _BOUNDS
from .errors import KakaoError, SendUncertain
from . import RID


class Driver:
    def __init__(self, dev: Device):
        self.dev = dev

    def screen(self) -> Screen:
        return Screen(self.dev.dump())

    def _screen_where(self, pred, timeout=15.0, interval=0.6) -> Screen | None:
        def ready():
            s = self.screen()
            return s if pred(s) else None
        return _wait_value(ready, timeout, interval)

    def _find(self, finder, timeout=15.0, interval=0.6) -> Node | None:
        return _wait_value(lambda: finder(self.screen()), timeout, interval)

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
                self.dev.shell("am force-stop com.kakao.talk")
            self.dev.shell("am start -n com.kakao.talk/.activity.SplashActivity")
            if _poll(_at_main, timeout=timeout):
                return  # caller waits for its own UI selector, not a fixed settle delay
        # give up silently; caller's _find will surface a precise error

    def open_chats_tab(self):
        last = None
        for attempt in range(3):
            try:
                self.ensure_main()
                s = self._screen_where(lambda s: _desc_prefix(s, "Chats tab"), timeout=8)
                if not s:
                    last = "Chats tab not found"; continue
                n = _desc_prefix(s, "Chats tab")
                if _selected(n) and _selected(_all_chip(s)) and s.first(rid=RID["room_list"]):
                    return
                if not _selected(n):
                    self.tap_node(n)
                # ensure the 'All' filter chip (not 'Unread'/'ChatGPT'/...) is active
                s = self._screen_where(_all_chip, timeout=6)
                if not s:
                    last = "All filter not visible"; continue
                allchip = _all_chip(s)
                if _selected(allchip) and s.first(rid=RID["room_list"]):
                    return
                if not _selected(allchip):
                    self.tap_node(allchip)
                if self._screen_where(lambda s: _selected(_all_chip(s)) and
                                      s.first(rid=RID["room_list"]) is not None, timeout=6):
                    return
                last = "room list not visible"
            except Exception as e:  # noqa
                last = str(e)
            time.sleep(1.0)
        raise KakaoError(f"open_chats_tab failed: {last}", activity=self.activity())

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

    def list_rooms(self, reads: int = 6) -> list[dict]:
        """Read the chat list, tolerating partial renders. The list only renders
        visible rows and re-sorts as messages arrive, so we re-dump a few times
        (without re-navigating) and return the most complete, stable snapshot."""
        self.open_chats_tab()
        best: list[dict] = []
        for i in range(reads):
            s = self._screen_where(lambda s: s.first(rid=RID["room_list"]) is not None, timeout=6)
            if s:
                rooms = self._read_room_list(s)
                if len(rooms) > len(best):   # keep the most complete render seen
                    best = rooms
            if i + 1 < reads:
                time.sleep(0.5)
        return best

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
        def _room(s):
            for nd in s.all():
                if nd.rid.endswith("/name") and (nd.text == name or nd.desc == name):
                    return nd
            return None
        last = None
        for attempt in range(3):
            try:
                self.open_chats_tab()
                n = self._find(_room, timeout=8)
                if not n:
                    last = f"room not found: {name}"; continue
                self.tap_node(n)
                if self._screen_where(lambda s: s.first(rid=RID["input"]) is not None, timeout=12):
                    return
                last = "chat room did not open"
                self.dev.back(); time.sleep(0.8)
            except Exception as e:  # noqa
                last = str(e)
            time.sleep(1.0)
        raise KakaoError(f"open_room failed: {last}", activity=self.activity())

    def open_self_chat(self):
        last = None
        for attempt in range(3):
            try:
                self.ensure_main()
                fn = self._find(lambda s: _desc_prefix(s, "Friends tab"), timeout=10)
                if fn:
                    self.tap_node(fn)
                av = self._find(lambda s: _desc_contains(s, "Profile photo"), timeout=10)
                if not av:
                    last = "profile avatar not found"; continue
                self.tap_node(av)
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
        field = s.first(rid=RID["input"])
        expected = field.text + text
        previous = self._messages_on_screen(s).count(expected)
        if field._el.get("focused") != "true":
            self.tap_node(field)
            if not self._find(lambda s: _focused_input(s), timeout=4):
                raise KakaoError("message input did not gain focus")
        self.dev.type_text(text)
        def committed(s):
            field = s.first(rid=RID["input"])
            button = s.first(rid=RID["send"])
            return button if (field and field.text == expected and button and
                              button._el.get("enabled") != "false") else None
        snd = self._find(committed, timeout=6)
        if not snd:
            raise KakaoError("text entry or send button not ready")
        self.tap_node(snd)
        try:
            confirmed = self._screen_where(lambda s: self._messages_on_screen(s).count(expected) > previous, timeout=6)
        except Exception:
            confirmed = None  # after submission, a failed readback is also uncertain
        if not confirmed:
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

    def _messages_detailed(self, s) -> list[dict]:
        """Ordered messages in the open room. Each dict has at least
        {kind: text|photo|video|file|date, text}; chat rows also carry
        {sender, is_mine} and media/file rows the tappable {bounds}. Fields
        confirmed in B0 only — time and read-count are not exposed by the app,
        so they are omitted rather than guessed. `sender` is empty on a
        consecutive message from the same person (the UI inherits it)."""
        out = []
        first = next(s.root.iter("node"), None)
        screen_w = _bounds_of(first)[2] if first is not None else 0
        conts = [el for el in s.root.iter("node")
                 if el.get("resource-id", "").endswith("chat_log_recycler_list")]
        for c in conts:
            for row in list(c):
                leafs: dict[str, str] = {}
                nodes: dict = {}
                for dd in row.iter("node"):
                    rid = dd.get("resource-id", "").split("/")[-1]
                    if rid and rid not in leafs:
                        leafs[rid] = (dd.get("text", "") or dd.get("content-desc", ""))
                        nodes[rid] = dd
                if not leafs:
                    continue
                if "time_line" in leafs:                      # date separator row
                    d = (leafs.get("time_line") or "").strip()
                    if d:
                        out.append({"kind": "date", "text": d})
                    continue
                sender = (leafs.get("nickname") or "").strip()
                # is_mine by geometry: my bubbles hug the right margin, others the
                # left column (row bounds are full-width, so use the bubble node)
                # (Elements without children are falsy — compare to None explicitly)
                mc = nodes.get("message_container")
                if mc is None:
                    mc = nodes.get("bubble")
                is_mine = False
                if mc is not None and screen_w:
                    is_mine = _bounds_of(mc)[2] >= screen_w * 0.93
                meta = {"sender": sender, "is_mine": is_mine}
                if "txt_size" in leafs:                       # file/document
                    name = (leafs.get("message") or leafs.get("bubble") or "").strip()
                    out.append({"kind": "file", "text": name or "[File]", "name": name,
                                "bounds": _bounds_of(nodes["bubble"] if "bubble" in nodes else row),
                                **meta})
                elif "image" in leafs:
                    out.append({"kind": "photo", "text": "[Photo]",
                                "bounds": _bounds_of(nodes["image"]), **meta})
                elif "video" in leafs:
                    out.append({"kind": "video", "text": "[Video]",
                                "bounds": _bounds_of(nodes["video"]), **meta})
                else:
                    t = ""
                    for k in ("message", "text", "text_with_pin"):
                        if (leafs.get(k) or "").strip():
                            t = leafs[k].strip(); break
                    if t:
                        out.append({"kind": "text", "text": t, **meta})
        if not out:  # fallback flat scan (text only)
            for n in s.all():
                if n.rid.split("/")[-1] in ("message", "text", "text_with_pin") and n.text.strip():
                    out.append({"kind": "text", "text": n.text.strip()})
        return out

    def _messages_on_screen(self, s) -> list[str]:
        """Back-compat: just the text of each visible message (media -> [Photo]/[Video])."""
        return [m["text"] for m in self._messages_detailed(s)]

    # --- receive: export a visible media/file bubble off the device ---
    KAKAO_SAVE_DIRS = ("/sdcard/Pictures/KakaoTalk", "/sdcard/DCIM/KakaoTalk",
                       "/sdcard/Movies/KakaoTalk", "/sdcard/Download/KakaoTalk")

    def _kakao_saves(self) -> set[str]:
        out = self.dev.shell("find %s -type f 2>/dev/null" % " ".join(self.KAKAO_SAVE_DIRS))
        return {l.strip() for l in out.splitlines() if l.strip()}

    def export_visible(self, bounds) -> str:
        """Tap a media/file bubble, Save it via the viewer, and return the device
        path of the newly-saved file. Privacy-safe: only ever touches the bubble
        the caller points at (in the open room). Re-anchors to the room after."""
        before = self._kakao_saves()
        x = (bounds[0] + bounds[2]) // 2
        y = (bounds[1] + bounds[3]) // 2
        self.dev.tap(x, y)
        # viewer (photo or file) exposes a "Save" control
        def _save(s):
            for n in s.all():
                if (n.desc == "Save" or n.text == "Save") and \
                   (n.bounds[2] - n.bounds[0]) > 10 and n.bounds[1] > 0:
                    return n
            return None
        sv = self._find(_save, timeout=10)
        if not sv:
            self.ensure_main()
            raise KakaoError("media viewer did not open or Save not found")
        self.tap_node(sv)
        newp = None
        end = time.time() + 25
        while time.time() < end:
            diff = self._kakao_saves() - before
            if diff:
                newp = sorted(diff)[-1]; break
            time.sleep(0.8)
        self.dev.back(); time.sleep(0.6)
        self.ensure_main()
        if not newp:
            raise SendUncertain("file saved but not detected on device")
        return newp

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
        from . import consolelock
        consolelock.check(self.dev)
        self._open_target(name)
        if not self._screen_where(lambda s: s.first(rid=RID["chat_log"]) is not None, timeout=10):
            raise KakaoError("chat log not found", room=name)
        prev_bottom = None
        primed = False
        while True:
            try:
                consolelock.check(self.dev)
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
            except KakaoError:
                raise
            except Exception:
                yield {"_error": "UI 수신 실패"}
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
        self.tap_node(plus)
        # scroll the sheet to reveal 'File', then tap it
        fnode = None
        for _ in range(4):
            fnode = self._find(lambda s: next((n for n in s.all()
                               if n.desc == "File" or n.text == "File"), None), timeout=1.5)
            if fnode:
                break
            self.dev.shell("input swipe 800 2300 800 1400 400")
        if not fnode:
            raise KakaoError("'File' option not found in + sheet")
        self.tap_node(fnode)
        # 'Select from File' -> system picker
        sel = self._find(lambda s: s.first(text="Select from File"), timeout=6)
        if not sel:
            raise KakaoError("'Select from File' not found")
        self.tap_node(sel)
        # DocumentsUI: find our file by name (search for robustness)
        srch = self._find(lambda s: next((n for n in s.all()
                        if n.rid.endswith("option_menu_search")), None), timeout=8)
        if srch:
            self.tap_node(srch)
            if not self._find(lambda s: next((n for n in s.all()
                              if n.rid.endswith("search_src_text") and
                              n._el.get("focused") == "true"), None), timeout=4):
                raise KakaoError("file search input did not gain focus")
            self.dev.type_text(name)

        # Pick the result row's title — NOT the search box, which now also holds
        # `name` (matching it taps the search field, selecting nothing).
        def _doc_item(s):
            for n in s.all():
                if n.rid.endswith("/title") and (name in n.text or name in n.desc):
                    return n
            for n in s.all():  # fallback: any match that isn't the search field
                if (name in n.desc or name in n.text) and not n.rid.endswith("search_src_text"):
                    return n
            return None
        item = self._find(_doc_item, timeout=10)
        if not item:
            self.dev.back(); time.sleep(0.5); self.dev.back()  # dismiss picker
            raise KakaoError("file not found in picker", file=name)
        self.tap_node(item)
        # Selecting the file dismisses DocumentsUI and returns to KakaoTalk, which
        # sends it. Confirm by waiting to leave the picker (while it is up no
        # KakaoTalk activity is top-resumed, so current_activity() is None).
        if not _poll(lambda: "com.kakao.talk" in (self.dev.current_activity() or ""), timeout=12):
            raise SendUncertain("document picker did not return to KakaoTalk", file=name)
        time.sleep(1.5)
        return "sent"


def _bounds_of(el) -> tuple[int, int, int, int]:
    m = _BOUNDS.search(el.get("bounds", "[0,0][0,0]"))
    return tuple(int(v) for v in m.groups()) if m else (0, 0, 0, 0)


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
    return bool(_wait_value(pred, timeout, interval))


def _wait_value(probe, timeout, interval):
    """Probe immediately; back off short waits without adding a settle sleep.

    A probe may itself be an expensive UI dump. Never issue overlapping dumps or
    sleep past the deadline; keep the caller's interval as the backoff ceiling.
    """
    if interval <= 0:
        raise ValueError("poll interval must be positive")
    end = time.monotonic() + timeout
    delay = min(0.1, interval)
    while time.monotonic() < end:
        value = probe()
        if value:
            return value
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        delay = min(interval, delay * 2)
    return None


def _selected(node):
    return bool(node and (node._el.get("selected") == "true" or node._el.get("checked") == "true"))


def _all_chip(screen):
    return next((n for n in screen.all() if n.rid.endswith("tab_chip") and n.text == "All"), None)


def _focused_input(screen):
    node = screen.first(rid=RID["input"])
    return node if node and node._el.get("focused") == "true" else None


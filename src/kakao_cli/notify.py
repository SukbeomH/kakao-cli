"""Receive stream via Android notifications (window-independent, low ban surface).

Parses `dumpsys notification --noredact` for com.kakao.talk records. KakaoTalk posts
a notification per incoming message (requires POST_NOTIFICATIONS, granted at setup).
Self-sent messages don't notify, so this is naturally receive-only. Muted/foreground
chats may not post — documented limitation; pair with chat-list polling if needed.
"""
from __future__ import annotations
import re
import time

from .adb import Device
from . import PACKAGE

_REC = re.compile(r"NotificationRecord\(.*?pkg=" + re.escape(PACKAGE) + r"\b", re.S)
_TITLE = re.compile(r"android\.title=(?:String \()?([^\n)]*)")
_TEXT = re.compile(r"android\.(?:text|bigText)=(?:String \()?([^\n)]*)")
_WHEN = re.compile(r"when=(\d+)")
_KEY = re.compile(r"key=(\S+)")


def _records(dump: str) -> list[str]:
    """Split the dumpsys blob into per-record chunks for our package."""
    out, idx = [], 0
    for m in _REC.finditer(dump):
        start = m.start()
        nxt = dump.find("NotificationRecord(", m.end())
        out.append(dump[start: nxt if nxt != -1 else len(dump)])
    return out


def parse(dump: str) -> list[dict]:
    events = []
    for rec in _records(dump):
        title = _TITLE.search(rec)
        text = _TEXT.search(rec)
        when = _WHEN.search(rec)
        key = _KEY.search(rec)
        if not text:
            continue
        events.append({
            "room": (title.group(1).strip() if title else ""),
            "text": text.group(1).strip(),
            "when": int(when.group(1)) if when else 0,
            "key": key.group(1) if key else "",
        })
    return events


def snapshot(dev: Device) -> list[dict]:
    return parse(dev.shell("dumpsys notification --noredact", timeout=25))


def watch(dev: Device, interval: float = 3.0):
    """Yield new message events as they appear. at-least-once; dedup by (key,when,text)."""
    seen: set = set()
    # prime without emitting backlog
    for e in snapshot(dev):
        seen.add((e["key"], e["when"], e["text"]))
    while True:
        try:
            for e in snapshot(dev):
                sig = (e["key"], e["when"], e["text"])
                if sig not in seen:
                    seen.add(sig)
                    yield e
        except Exception as e:  # transient adb hiccup — keep watching
            yield {"_error": str(e)}
        time.sleep(interval)

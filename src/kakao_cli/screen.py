"""Live device-screen view/control helpers (for re-login over the web UI).

Pure, testable bits (coordinate transform, login detection, PNG sizing) plus a
small thread-safe frame buffer. The actual screencap/tap/swipe run through the
Worker so all device access stays serialized.
"""
from __future__ import annotations
import struct
import threading
import time


def png_size(data: bytes):
    """(width, height) from a PNG's IHDR, or None if not a PNG."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return None


def view_to_device(cx, cy, ew, eh, dev_w, dev_h):
    """Map a click at (cx, cy) inside a (ew × eh) <img> element — which shows a
    (dev_w × dev_h) frame with object-fit: contain (letterboxed, centered) — to
    device pixels. screencap already reflects the device's current rotation, so
    the frame and `input tap` share one coordinate space; no extra rotation is
    applied. Returns clamped integer device coords."""
    ew = float(ew); eh = float(eh)
    if ew <= 0 or eh <= 0 or dev_w <= 0 or dev_h <= 0:
        return 0, 0
    scale = min(ew / dev_w, eh / dev_h)      # contain
    cw, ch = dev_w * scale, dev_h * scale     # displayed content size
    ox, oy = (ew - cw) / 2.0, (eh - ch) / 2.0  # letterbox offsets
    dx = (cx - ox) / scale
    dy = (cy - oy) / scale
    dx = max(0, min(dev_w - 1, dx))
    dy = max(0, min(dev_h - 1, dy))
    return int(round(dx)), int(round(dy))


# login / onboarding activities & resource-ids (from the documented login contract)
_LOGIN_ACT_HINTS = ("Authenticator", "AccountManage", "SignupQr", "LoginActivity")
_LOGIN_RID_HINTS = ("signup_kakao_account_with_qr", "chk_subdevice",
                    "login_kakao_account", "account_key")


def is_login_screen(activity: str, xml: str = "") -> bool:
    """True when the app is showing a login / sub-device onboarding screen."""
    a = activity or ""
    if any(h in a for h in _LOGIN_ACT_HINTS):
        return True
    x = xml or ""
    return any(rid in x for rid in _LOGIN_RID_HINTS)


class ScreenBuffer:
    """Latest captured frame + a viewer-liveness clock. The worker captures only
    while a viewer is active (a frame was fetched within `ttl` seconds), so no
    capture happens when nobody is watching."""

    def __init__(self, ttl: float = 2.0):
        self.lock = threading.Lock()
        self.frame: bytes = b""
        self.seq = 0
        self.w = 0
        self.h = 0
        self.ttl = ttl
        self.last_access = 0.0

    def set_frame(self, data: bytes):
        sz = png_size(data)
        with self.lock:
            if data == self.frame:
                return          # identical frame → don't bump (saves bandwidth)
            self.frame = data
            self.seq += 1
            if sz:
                self.w, self.h = sz

    def touch(self):
        with self.lock:
            self.last_access = time.time()

    def active(self) -> bool:
        with self.lock:
            return (time.time() - self.last_access) < self.ttl

    def get(self):
        with self.lock:
            return self.seq, self.frame, self.w, self.h

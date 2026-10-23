"""Live H.264 video for the web console (scrcpy video socket → browsers).

VideoHub owns the "video on/off" decision for the shared ScrcpySession:
  * first viewer  → restart the session with video (virtual display or mirror)
  * last viewer   → after a grace period, restart control-only (the virtual
                    display is destroyed; KakaoTalk moves back to display 0)
Virtual mode (default) creates a scrcpy virtual display sized to the browser's
screen area (CSS px × devicePixelRatio, dpi = 160 × dpr so 1 dp ≈ 1 CSS px) and
starts KakaoTalk on it; later browser resizes are RESIZE_DISPLAY (flex). If the
virtual display can't come up, it falls back to mirroring display 0.
While a virtual display is live, a device-side lock tells CLI/TUI to back off.

Viewers get events through bounded queues; a slow viewer drops frames until the
next keyframe (never mid-GOP) and a fresh keyframe is requested on join.
"""
from __future__ import annotations
import queue
from collections import deque
import threading
import time

from . import consolelock
from .scrcpy import enc_reset_video, enc_start_app, enc_resize_display

APP = "com.kakao.talk"
GRACE_S = 5.0
VD_TIMEOUT_S = 8.0
MAX_VD_SIDE = 1920
MIN_VD_SIDE = 320
FRAME_AGE_S = 0.250
QUALITY_SIDES = (960, 1280, 1440, 1920)


def vd_geometry(css_w: float, css_h: float, dpr: float, dpi_mode="auto",
                max_side: int = MAX_VD_SIDE):
    """Browser screen area → (w, h, dpi) for the virtual display.
    auto: w,h = CSS px × dpr, dpi = 160 × dpr (1 dp ≈ 1 CSS px: desktop gets the
    tablet layout, phones a phone layout at native text size); scaled down
    proportionally (size and dpi) if the long side exceeds max_side.
    'device': dpi proportional to the real device (340 dpi over 1600 px wide);
    an int: fixed dpi. Sizes are rounded to multiples of 8 (encoder friendly)."""
    dpr = max(0.5, min(4.0, float(dpr or 1)))
    w, h = max(1.0, float(css_w)) * dpr, max(1.0, float(css_h)) * dpr
    scale = min(1.0, max_side / max(w, h))
    w, h = w * scale, h * scale
    w = max(MIN_VD_SIDE, int(round(w / 8.0)) * 8)
    h = max(MIN_VD_SIDE, int(round(h / 8.0)) * 8)
    if dpi_mode == "device":
        dpi = int(round(340 * min(w, h) / 1600.0))
    elif isinstance(dpi_mode, int) or str(dpi_mode).isdigit():
        dpi = int(dpi_mode)
    else:
        dpi = int(round(160 * dpr * scale))
    return w, h, max(80, min(640, dpi))


class FrameQueue(queue.Queue):
    """Track local enqueue time; device PTS is not a host wall clock."""
    def _init(self, maxsize):
        super()._init(maxsize)
        self.times = deque()

    def _put(self, item):
        super()._put(item)
        self.times.append(time.monotonic() if item[0] == "frame" else None)

    def _get(self):
        self.times.popleft()
        return super()._get()

    def stale(self):
        with self.mutex:
            return any(t is not None and time.monotonic() - t > FRAME_AGE_S for t in self.times)


class Sub:
    def __init__(self, fmt: str):
        self.fmt = fmt
        self.q = FrameQueue(maxsize=32)
        self.need_key = True
        self.waiting_since = time.time()

    def put(self, ev) -> bool:
        try:
            self.q.put_nowait(ev)
            return True
        except queue.Full:
            return False


class VideoHub:
    def __init__(self, bridge, dev, mode: str = "virtual", max_fps: int = 60,
                 bit_rate: int = 4_000_000, max_size: int = 1280, dpi_mode="auto", vd_max_size: int = 1280):
        self.bridge = bridge
        self.dev = dev
        self.lock = threading.Lock()
        self.subs: list[Sub] = []
        self.mode = mode if mode in ("virtual", "mirror") else "virtual"
        self.max_fps, self.bit_rate, self.max_size, self.dpi_mode = max_fps, bit_rate, max_size, dpi_mode
        if vd_max_size not in QUALITY_SIDES:
            raise ValueError("vd_max_size must be 960, 1280, 1440 or 1920")
        self.vd_max_size = vd_max_size
        self.css_size = None
        self.vd = None                 # (w, h, dpi) requested for the virtual display
        self.wh = None                 # current stream size (session packet)
        self.config = None             # latest SPS/PPS (Annex-B)
        self.active = False            # video on (requested)
        self.note = ""
        self._grace = None
        self._vd_deadline = None
        self._session_seen = 0.0
        self.frames = 0
        self.bytes = 0
        self._rate = (time.time(), 0, 0, 0.0, 0.0)   # t, frames, bytes, fps, kbps
        self._last_reset = 0.0
        self._lock_t = 0.0
        bridge.on_video = self._on_event
        prev_connect = bridge.on_connect
        def on_connect():
            if prev_connect:
                prev_connect()
            self._on_connect()
        bridge.on_connect = on_connect
        self._hb = threading.Thread(target=self._heartbeat, daemon=True)
        self._hb.start()

    # ---------------------------------------------------------------- control
    def _opts(self):
        o = {"max_fps": self.max_fps, "video_bit_rate": self.bit_rate,
             "video_codec_options": "i-frame-interval=1"}
        if self.mode == "virtual" and self.vd:
            w, h, dpi = self.vd
            o.update({"new_display": "%dx%d/%d" % (w, h, dpi), "flex_display": "true",
                      "vd_destroy_content": "false", "vd_system_decorations": "false",
                      "display_ime_policy": "local"})
        else:
            o["max_size"] = self.max_size
        return o

    def _start_video(self):
        self.active = True
        self.wh = None
        self.config = None
        self._vd_deadline = time.time() + VD_TIMEOUT_S if self.mode == "virtual" else None
        self.bridge.set_video(True, **self._opts())

    def _on_connect(self):
        if self.active and self.mode == "virtual" and self.bridge.video:
            self.bridge.send(enc_start_app(APP))        # put KakaoTalk on the new display

    def subscribe(self, fmt: str, size=None) -> Sub:
        sub = Sub(fmt)
        with self.lock:
            if self._grace:
                self._grace.cancel()
                self._grace = None
            self.subs.append(sub)
            first = not self.active
            if size and self.mode == "virtual" and (first or self.vd is None):
                self.css_size = size
                self.vd = vd_geometry(*size, dpi_mode=self.dpi_mode, max_side=self.vd_max_size)
            if not first:
                if self.wh:
                    sub.put(("session",) + self.wh)
                if self.config:
                    sub.put(("config", self.config))
        if first:
            self._start_video()
        else:
            self._request_key()
        return sub

    def unsubscribe(self, sub: Sub):
        with self.lock:
            if sub in self.subs:
                self.subs.remove(sub)
            if not self.subs and self.active and not self._grace:
                self._grace = threading.Timer(GRACE_S, self._stop_if_idle)
                self._grace.daemon = True
                self._grace.start()

    def _stop_if_idle(self):
        with self.lock:
            self._grace = None
            if self.subs:
                return
            self.active = False
            self.wh = self.config = None
        self.bridge.set_video(False)       # destroys the virtual display (app → display 0)
        try:
            consolelock.clear(self.dev)
        except Exception:
            pass

    def resize(self, css_w, css_h, dpr) -> dict:
        """Flex: follow the browser's screen area (virtual mode only)."""
        if self.mode != "virtual":
            return {"mode": self.mode}
        self.css_size = (css_w, css_h, dpr)
        w, h, dpi = vd_geometry(css_w, css_h, dpr, dpi_mode=self.dpi_mode, max_side=self.vd_max_size)
        with self.lock:
            old = self.vd
            self.vd = (w, h, dpi)
        if not self.active:
            return {"w": w, "h": h, "dpi": dpi, "applied": False}
        if old and old[2] != dpi:                  # dpi only changes by restarting
            self._start_video()
        elif not old or (old[0], old[1]) != (w, h):
            try:
                self.bridge.send(enc_resize_display(w, h))
            except Exception as e:
                return {"error": str(e)}
        return {"w": w, "h": h, "dpi": dpi, "applied": True}

    def set_quality(self, max_side):
        if isinstance(max_side, bool) or max_side not in QUALITY_SIDES:
            raise ValueError("max_side must be 960, 1280, 1440 or 1920")
        if self.vd_max_size == max_side:
            return self.info()
        self.vd_max_size = int(max_side)
        if self.css_size:
            # Also remember geometry while mirroring, for the next virtual session.
            self.vd = vd_geometry(*self.css_size, dpi_mode=self.dpi_mode, max_side=self.vd_max_size)
            if self.mode == "virtual" and self.active:
                self._start_video()
        return self.info()

    def set_mode(self, mode: str) -> dict:
        if mode not in ("virtual", "mirror"):
            raise ValueError("mode must be virtual or mirror")
        with self.lock:
            changed = mode != self.mode
            self.mode = mode
            self.note = ""
        if changed and self.active:
            self._start_video()
        if mode == "mirror":
            try:
                consolelock.clear(self.dev)
            except Exception:
                pass
        return self.info()

    def _fallback(self, why: str):
        self.note = "가상 디스플레이 실패(%s) → 미러" % why
        with self.lock:
            self.mode = "mirror"
        try:
            consolelock.clear(self.dev)
        except Exception:
            pass
        if self.active:
            self._start_video()

    def _request_key(self):
        now = time.time()
        if now - self._last_reset > 0.5:
            self._last_reset = now
            try:
                self.bridge.send(enc_reset_video())
            except Exception:
                pass

    # ------------------------------------------------------------ fan-out
    def _on_event(self, ev):
        kind = ev[0]
        if kind == "codec":
            return
        if kind == "session":
            self.wh = (ev[1], ev[2])
            self.config = None
            self._session_seen = time.time()
            self._vd_deadline = None
        elif kind == "config":
            self.config = ev[1]
        elif kind == "frame":
            self.frames += 1
            self.bytes += len(ev[3])
        with self.lock:
            subs = list(self.subs)
        for sub in subs:
            if kind == "frame":
                if sub.q.stale():
                    self._resync(sub)
                if sub.need_key:
                    if not ev[2]:
                        if time.time() - sub.waiting_since > 1.0:
                            self._request_key()
                        continue
                    sub.need_key = False
                if not sub.put(ev):                # slow viewer: resync on the next key
                    self._resync(sub)
                    if ev[2]:
                        sub.need_key = False
                        sub.put(ev)
            else:
                if kind == "session":
                    self._resync(sub, request=False)
                else:
                    sub.put(ev)

    def _resync(self, sub, request=True):
        sub.need_key = True
        sub.waiting_since = time.time()
        try:
            while True:
                sub.q.get_nowait()
        except queue.Empty:
            pass
        if self.wh:
            sub.put(("session",) + self.wh)
        if self.config:
            sub.put(("config", self.config))
        if request:
            self._request_key()

    # ------------------------------------------------------- housekeeping
    def _heartbeat(self):
        while True:
            time.sleep(1.0)
            t, f0, b0, fps, kbps = self._rate
            now = time.time()
            if now - t >= 2.0:
                self._rate = (now, self.frames, self.bytes,
                              (self.frames - f0) / (now - t), (self.bytes - b0) * 8 / 1000.0 / (now - t))
            if (self.active and self.mode == "virtual" and self._vd_deadline
                    and now > self._vd_deadline and not self.wh):
                self._vd_deadline = None
                self._fallback("no stream in %ds" % VD_TIMEOUT_S)
            if self.active and self.mode == "virtual" and self.bridge.connected and self.wh:
                if now - self._lock_t >= 5.0:
                    self._lock_t = now
                    try:
                        consolelock.write(self.dev, "virtual")
                    except Exception:
                        pass
            else:
                self._lock_t = 0.0

    def info(self) -> dict:
        _t, _f, _b, fps, kbps = self._rate
        with self.lock:
            viewers = len(self.subs)
        return {"mode": self.mode, "active": self.active, "viewers": viewers,
                "w": self.wh[0] if self.wh else None, "h": self.wh[1] if self.wh else None,
                "vd": list(self.vd) if self.vd else None, "fps": round(fps, 1),
                "kbps": round(kbps), "note": self.note,
                "max_side": self.vd_max_size, "max_fps": self.max_fps,
                "connected": bool(self.bridge.connected and self.bridge.video)}

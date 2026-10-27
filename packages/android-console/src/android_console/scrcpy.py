"""scrcpy-server bridge for the web console (clipboard, input, H.264 video).

Third-party component: scrcpy-server (Genymobile, Apache-2.0), pinned to
v5.0.1 and verified by sha256. It is *not* committed — on first use it is
downloaded from the official GitHub release into ~/.cache/android-console/ (or taken
from --scrcpy-server PATH for offline use) and rejected on any hash mismatch.

Protocol (v5.0.1, from the tagged sources):
  control  →device  INJECT_KEYCODE  [0][action u8][keycode i32][repeat i32][meta i32]
                    INJECT_TOUCH    [2][action u8][pointer i64][x i32][y i32]
                                    [w u16][h u16][pressure u16][actionBtn i32][buttons i32]
                    SET_CLIPBOARD   [9][seq i64][paste u8][len i32][utf8]
                    START_APP       [16][len u8][utf8]   RESET_VIDEO [17]
                    RESIZE_DISPLAY  [21][w u16][h u16]   SCAN_FILE [22][len i32][utf8]
  control  ←device  CLIPBOARD [0][len i32][utf8]   ACK_CLIPBOARD [1][seq i64]
                    UHID_OUTPUT [2][id u16][len u16][data]
  video    ←device  codec id u32, then 12-byte headers: top bit set → session
                    packet (flags u32, width u32, height u32); else
                    pts|flags u64 (bit62 config, bit61 key) + size u32 + payload.
Touch positions are in *video* coordinates and w/h must equal the current
video size, otherwise the server ignores the event.
"""
from __future__ import annotations
import hashlib
import os
import random
import socket
import struct
import subprocess
import threading
import time
import urllib.request

VERSION = "5.0.1"
SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"
URL = ("https://github.com/Genymobile/scrcpy/releases/download/v%s/scrcpy-server-v%s"
       % (VERSION, VERSION))
DEVICE_JAR = "/data/local/tmp/android-console-scrcpy-server"
CLIP_MAX = (1 << 18) - 14          # SET_CLIPBOARD must fit the server's 256 KiB message cap

# Android constants
ACTION_DOWN, ACTION_UP, ACTION_MOVE, ACTION_CANCEL = 0, 1, 2, 3


class ScrcpyError(RuntimeError):
    pass


# ----------------------------------------------------------------- server jar
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_server(path: str | None = None, cache_dir: str = "~/.cache/android-console",
                  fetch=None) -> str:
    """Return a local scrcpy-server path whose sha256 matches the pin.
    `path` (from --scrcpy-server) is verified as-is; otherwise the cached copy is
    used or downloaded once. Any mismatch raises ScrcpyError."""
    if path:
        if not os.path.isfile(path):
            raise ScrcpyError("scrcpy-server not found: %s" % path)
        got = sha256_file(path)
        if got != SHA256:
            raise ScrcpyError("scrcpy-server sha256 mismatch (%s…), refusing" % got[:12])
        return path
    cache = os.path.expanduser(cache_dir)
    dest = os.path.join(cache, "scrcpy-server-v%s" % VERSION)
    if os.path.isfile(dest) and sha256_file(dest) == SHA256:
        return dest
    os.makedirs(cache, exist_ok=True)
    part = dest + ".part"
    try:
        (fetch or _download)(URL, part)
        got = sha256_file(part)
        if got != SHA256:
            raise ScrcpyError("downloaded scrcpy-server sha256 mismatch (%s…), refusing" % got[:12])
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.unlink(part)
    return dest


def _download(url: str, dest: str):
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


# ------------------------------------------------------------- control encode
def enc_keycode(action: int, keycode: int, repeat: int = 0, meta: int = 0) -> bytes:
    return struct.pack(">BBiii", 0, action, keycode, repeat, meta)


def enc_touch(action: int, x: int, y: int, w: int, h: int,
              pressure: float = 1.0, pointer_id: int = -2, *, action_button=0, buttons=0) -> bytes:
    p = 0 if action in (ACTION_UP, ACTION_CANCEL) else max(0, min(0xFFFF, int(pressure * 0xFFFF)))
    # pointer -2 = scrcpy's generic finger (touchscreen source); buttons are
    # only for mouse events (-1), so touch callers keep the defaults at 0
    return struct.pack(">BBqiiHHHii", 2, action, pointer_id, int(x), int(y),
                       int(w), int(h), p, action_button, buttons)


def enc_secondary_click(x: int, y: int, w: int, h: int) -> bytes:
    """Android mouse secondary-button click, without a synthetic touch/long press."""
    return (enc_touch(ACTION_DOWN, x, y, w, h, pointer_id=-1, action_button=2, buttons=2)
            + enc_touch(ACTION_UP, x, y, w, h, pointer_id=-1, action_button=2, buttons=0))


def enc_scroll(x: int, y: int, w: int, h: int, dx: float, dy: float) -> bytes:
    """scrcpy v5: axes in [-16,16], normalized to signed 16-bit fixed point."""
    def fixed(v):
        return max(-32768, min(32767, int(float(v) / 16 * 32768)))
    return struct.pack(">BiiHHhhI", 3, int(x), int(y), int(w), int(h), fixed(dx), fixed(dy), 0)


def enc_cancel_touch(x: int, y: int, w: int, h: int) -> bytes:
    # CANCEL ends the Android gesture without a click. scrcpy v5's PointersState
    # only removes fingers on UP, so also clear its bookkeeping afterwards.
    return enc_touch(ACTION_CANCEL, x, y, w, h) + enc_touch(ACTION_UP, x, y, w, h)


def enc_get_clipboard() -> bytes:
    # GET_CLIPBOARD, COPY_KEY_NONE: read the clipboard without copying/cutting a selection.
    return b"\x08\x00"


def enc_set_clipboard(seq: int, text: str, paste: bool) -> bytes:
    data = text.encode("utf-8")
    if len(data) > CLIP_MAX:
        raise ValueError("clipboard text too long (%d bytes, max %d)" % (len(data), CLIP_MAX))
    return struct.pack(">BqBi", 9, seq, 1 if paste else 0, len(data)) + data


def enc_reset_video() -> bytes:
    return b"\x11"


def enc_start_app(name: str) -> bytes:
    data = name.encode("utf-8")
    if len(data) > 255:
        raise ValueError("app name too long")
    return struct.pack(">BB", 16, len(data)) + data


def enc_resize_display(w: int, h: int) -> bytes:
    return struct.pack(">BHH", 21, int(w), int(h))


def enc_scan_file(path: str) -> bytes:
    data = path.encode("utf-8")
    return struct.pack(">Bi", 22, len(data)) + data


# ----------------------------------------------------------- device → client
class DeviceMsgParser:
    """Incremental parser for the control socket's device messages."""

    def __init__(self):
        self.buf = b""

    def feed(self, data: bytes) -> list:
        self.buf += data
        out = []
        while self.buf:
            t = self.buf[0]
            if t == 0:                                   # CLIPBOARD
                if len(self.buf) < 5:
                    break
                n = struct.unpack(">i", self.buf[1:5])[0]
                if len(self.buf) < 5 + n:
                    break
                out.append(("clipboard", self.buf[5:5 + n].decode("utf-8", "replace")))
                self.buf = self.buf[5 + n:]
            elif t == 1:                                 # ACK_CLIPBOARD
                if len(self.buf) < 9:
                    break
                out.append(("ack", struct.unpack(">q", self.buf[1:9])[0]))
                self.buf = self.buf[9:]
            elif t == 2:                                 # UHID_OUTPUT (ignored)
                if len(self.buf) < 5:
                    break
                n = struct.unpack(">H", self.buf[3:5])[0]
                if len(self.buf) < 5 + n:
                    break
                self.buf = self.buf[5 + n:]
            else:
                raise ScrcpyError("unknown device message type %d" % t)
        return out


class VideoParser:
    """Incremental parser for the v5 video stream (see module docstring)."""

    def __init__(self):
        self.buf = b""
        self.codec = None

    def feed(self, data: bytes) -> list:
        self.buf += data
        out = []
        if self.codec is None:
            if len(self.buf) < 4:
                return out
            self.codec = struct.unpack(">I", self.buf[:4])[0]
            self.buf = self.buf[4:]
            out.append(("codec", self.codec))
        while len(self.buf) >= 12:
            if self.buf[0] & 0x80:                       # session packet
                flags, w, h = struct.unpack(">III", self.buf[:12])
                self.buf = self.buf[12:]
                out.append(("session", w, h, flags & 1))
                continue
            pf, n = struct.unpack(">QI", self.buf[:12])
            if len(self.buf) < 12 + n:
                break
            payload = self.buf[12:12 + n]
            self.buf = self.buf[12 + n:]
            if (pf >> 62) & 1:
                out.append(("config", payload))
            else:
                out.append(("frame", pf & ((1 << 61) - 1), bool((pf >> 61) & 1), payload))
        return out


def avc_codec_string(sps_nal: bytes) -> str:
    """'avc1.PPCCLL' from an SPS NAL (start code optional)."""
    i = 0
    if sps_nal.startswith(b"\x00\x00\x00\x01"):
        i = 4
    elif sps_nal.startswith(b"\x00\x00\x01"):
        i = 3
    p, c, l = sps_nal[i + 1], sps_nal[i + 2], sps_nal[i + 3]
    return "avc1.%02X%02X%02X" % (p, c, l)


# -------------------------------------------------------------------- session
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _recvn(sock, n: int) -> bytes:
    b = b""
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c:
            raise EOFError
        b += c
    return b


class ScrcpySession:
    """One scrcpy-server instance (control, optionally + video) kept alive for
    the web console's lifetime, restarted with backoff if it dies.

    Clipboard text from the device is kept in memory only (latest one); it is
    never logged or written to disk.
    """

    def __init__(self, dev, server_path: str, on_change=None):
        self.dev = dev
        self.server_path = server_path
        import uuid
        self.device_jar = DEVICE_JAR + "-" + uuid.uuid4().hex + ".jar"
        self.on_change = on_change or (lambda: None)
        self.lock = threading.Lock()          # one writer on the control socket
        self.state_lock = threading.Lock()
        self._clip_changed = threading.Condition(self.state_lock)
        self._clip_read_lock = threading.Lock()
        self._clip_generation = 0
        self._clip_uncertain = False
        self._clip_poller = None
        self.proc = None
        self.port = None
        self.scid = None
        self.ctrl = None
        self.vsock = None
        self.connected = False
        self.clip_seq = 0
        self.clip_text = ""
        self._ack = {}
        self._set_seq = 1
        self._stop = threading.Event()
        self._pushed = False
        self.error = None
        self.video = False
        self.video_opts = {}
        self.on_video = None                  # callback(event) for the video hub
        self.on_connect = None                # callback() after each successful (re)connect
        self._restart = threading.Event()
        self._sup = None

    # -- lifecycle ------------------------------------------------------------
    def start(self):
        self._sup = threading.Thread(target=self._supervise, daemon=True)
        self._sup.start()
        self._clip_poller = threading.Thread(target=self._poll_clipboard, daemon=True)
        self._clip_poller.start()

    def set_video(self, enabled: bool, **opts):
        """Restart the server with/without the video socket (keeps control)."""
        with self.state_lock:
            if enabled == self.video and (not enabled or opts == self.video_opts):
                return
            self.video, self.video_opts = enabled, dict(opts)
        self._restart.set()
        self._close()

    def stop(self):
        self._stop.set()
        self._restart.set()
        self._close()
        if self._sup:                       # let an in-flight connect finish, then
            self._sup.join(timeout=8)       # close again so nothing is left behind
        self._close()
        self._cleanup_device()
        if self._clip_poller:
            self._clip_poller.join(timeout=2)

    def _supervise(self):
        backoff = 1.0
        while not self._stop.is_set():
            self._restart.clear()
            try:
                self._connect()
                backoff = 1.0
                self.error = None
                if self.on_connect:
                    try:
                        self.on_connect()
                    except Exception as e:   # noqa — report, keep the session
                        self.error = "on_connect: %s" % e
                self.on_change()
                # wait until the session dies, a restart is requested, or stop
                while not self._stop.is_set() and not self._restart.is_set():
                    if self.proc and self.proc.poll() is not None:
                        raise ScrcpyError("scrcpy-server exited")
                    if not self.connected:
                        raise ScrcpyError("scrcpy connection lost")
                    time.sleep(0.3)
            except Exception as e:  # noqa — keep the console alive
                self.error = str(e)
            self._close()
            self.on_change()
            if self._stop.is_set():
                break
            if not self._restart.is_set():
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 2)

    def _connect(self):
        d = self.dev
        d.push(self.server_path, self.device_jar)   # cheap (≈730 KB over adb); survives jar loss
        self._pushed = True
        self.scid = "%08x" % random.randrange(1, 0x7FFFFFFF)
        self.port = _free_port()
        d._run(["forward", "tcp:%d" % self.port, "localabstract:scrcpy_" + self.scid], check=True)
        video = self.video
        args = ["scid=" + self.scid, "log_level=warn", "tunnel_forward=true", "audio=false",
                "control=true", "clipboard_autosync=false", "cleanup=false",
                "power_off_on_close=false", "send_device_meta=false", "send_dummy_byte=true",
                "video=" + ("true" if video else "false")]
        if video:
            args += ["send_stream_meta=true", "send_frame_meta=true", "video_codec=h264"]
            args += ["%s=%s" % (k, v) for k, v in self.video_opts.items() if v not in (None, "")]
        cmd = ("CLASSPATH=%s app_process / com.genymobile.scrcpy.Server %s %s"
               % (self.device_jar, VERSION, " ".join(args)))
        self.proc = subprocess.Popen([d.adb, "-s", d.serial, "shell", cmd],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL)
        first = None
        for _ in range(100):                   # forward accepts before the server listens
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                _recvn(s, 1)                   # dummy byte = server really there
                first = s
                break
            except (EOFError, ConnectionResetError, socket.timeout):
                s.close()
                if self.proc.poll() is not None:
                    raise ScrcpyError("scrcpy-server failed to start")
                time.sleep(0.1)
        if first is None:
            raise ScrcpyError("scrcpy-server did not come up")
        if video:
            self.vsock = first
            self.ctrl = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        else:
            self.ctrl = first
        self.ctrl.settimeout(None)
        with self._clip_changed:
            self.connected = True
            self._clip_uncertain = False
            self._clip_changed.notify_all()
        threading.Thread(target=self._read_ctrl, args=(self.ctrl,), daemon=True).start()
        if video:
            self.vsock.settimeout(None)
            threading.Thread(target=self._read_video, args=(self.vsock,), daemon=True).start()

    def _close(self):
        self.connected = False
        with self._clip_changed:
            self._clip_changed.notify_all()
        for s in (self.ctrl, self.vsock):
            if s:
                try:
                    s.close()
                except OSError:
                    pass
        self.ctrl = self.vsock = None
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=3)
            except Exception:
                pass
        self.proc = None
        if self.port:
            try:
                self.dev._run(["forward", "--remove", "tcp:%d" % self.port])
            except Exception:
                pass
            self.port = None

    def _cleanup_device(self):
        try:
            self.dev.shell("rm -f %s" % self.device_jar)
        except Exception:
            pass
        self._pushed = False

    # -- readers -------------------------------------------------------------
    def _read_ctrl(self, sock):
        parser = DeviceMsgParser()
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                for ev in parser.feed(data):
                    if ev[0] == "clipboard":
                        with self._clip_changed:
                            if sock is not self.ctrl:
                                return
                            changed = ev[1] != self.clip_text
                            if ev[1] != self.clip_text:
                                self.clip_text = ev[1]
                                self.clip_seq += 1
                            self._clip_generation += 1
                            self._clip_changed.notify_all()
                        if changed:
                            self.on_change()
                    elif ev[0] == "ack":
                        e = self._ack.pop(ev[1], None)
                        if e:
                            e.set()
        except (OSError, ScrcpyError):
            pass
        if sock is self.ctrl:
            self.connected = False
            with self._clip_changed:
                self._clip_changed.notify_all()

    def _read_video(self, sock):
        parser = VideoParser()
        try:
            while True:
                data = sock.recv(1 << 16)
                if not data:
                    break
                for ev in parser.feed(data):
                    cb = self.on_video
                    if cb:
                        cb(ev)
        except (OSError, ScrcpyError):
            pass
        if sock is self.vsock:
            self.connected = False

    # -- commands ------------------------------------------------------------
    def get_clipboard(self, timeout: float = 3.0, *, _poll=False):
        """Request clipboard text, including unchanged text, instead of returning cache.

        scrcpy suppresses explicit GET replies when clipboard_autosync is enabled.
        Disable that option and serialize reads (the protocol has no request ID).
        Explicit reads retire an ambiguous connection after a prior timeout, so
        a late reply cannot satisfy a later user request. Background reads may
        time out on an unset Android clipboard; they must not restart the video.
        """
        deadline = time.monotonic() + timeout
        if not self._clip_read_lock.acquire(timeout=max(0, timeout)):
            raise TimeoutError("clipboard read busy")
        try:
            if not _poll:
                with self.state_lock:
                    uncertain, previous = self._clip_uncertain, self.ctrl
                if uncertain:
                    self._close()
                    self._restart.set()
                    with self._clip_changed:
                        ready = self._clip_changed.wait_for(
                            lambda: self.connected and self.ctrl is not previous or self._stop.is_set(),
                            timeout=max(0, deadline - time.monotonic()))
                        if not ready or self._stop.is_set():
                            raise TimeoutError("clipboard reconnect timed out")
            with self._clip_changed:
                sock, generation = self.ctrl, self._clip_generation
            self.send(enc_get_clipboard())
            with self._clip_changed:
                self._clip_changed.wait_for(
                    lambda: self._clip_generation != generation or not self.connected or self.ctrl is not sock,
                    timeout=max(0, deadline - time.monotonic()))
                if not self.connected or self.ctrl is not sock:
                    raise ScrcpyError("clipboard connection closed")
                if self._clip_generation != generation:
                    return self.clip_seq, self.clip_text
                self._clip_uncertain = True
            if not _poll:
                self._close()
                self._restart.set()
            raise TimeoutError("clipboard read timed out")
        finally:
            self._clip_read_lock.release()

    def _poll_clipboard(self):
        # Keep the existing copy notification/opt-in auto-copy feature available.
        # This uses the control channel only, without ADB or screen captures.
        while not self._stop.wait(1.0):
            if self.connected:
                try:
                    self.get_clipboard(timeout=1.0, _poll=True)
                except (ScrcpyError, TimeoutError, OSError):
                    pass

    def send(self, data: bytes):
        with self.lock:
            if not self.connected or not self.ctrl:
                raise ScrcpyError("scrcpy not connected")
            self.ctrl.sendall(data)

    def set_clipboard(self, text: str, paste: bool = False, timeout: float = 3.0) -> bool:
        with self.state_lock:
            seq = self._set_seq
            self._set_seq += 1
        ev = threading.Event()
        self._ack[seq] = ev
        try:
            self.send(enc_set_clipboard(seq, text, paste))
            ok = ev.wait(timeout)
        finally:
            self._ack.pop(seq, None)
        if ok:
            with self.state_lock:
                self.clip_text = text
        return ok

    def tap(self, x: int, y: int, w: int, h: int):
        self.send(enc_touch(ACTION_DOWN, x, y, w, h) + enc_touch(ACTION_UP, x, y, w, h))

    def swipe(self, x1, y1, x2, y2, w, h, ms: int = 250, steps: int = 8):
        """down → interpolated moves over `ms` → up (runs in its own thread)."""
        def run():
            try:
                self.send(enc_touch(ACTION_DOWN, x1, y1, w, h))
                for i in range(1, steps + 1):
                    time.sleep(ms / 1000.0 / steps)
                    self.send(enc_touch(ACTION_MOVE, x1 + (x2 - x1) * i // steps,
                                        y1 + (y2 - y1) * i // steps, w, h))
                self.send(enc_touch(ACTION_UP, x2, y2, w, h))
            except ScrcpyError:
                pass
        threading.Thread(target=run, daemon=True).start()

    def key(self, keycode: int):
        self.send(enc_keycode(ACTION_DOWN, keycode) + enc_keycode(ACTION_UP, keycode))

    def clip(self):
        with self.state_lock:
            return self.clip_seq, self.clip_text

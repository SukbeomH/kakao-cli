"""Console video (V/V2): fMP4 muxer, virtual-display geometry, VideoHub fan-out,
console lock, CLI refusal while the console holds the device, video endpoints
and input routing (scrcpy on the streamed display vs adb on display 0)."""
import http.client
import json
import struct
import sys
import threading
import time

sys.path.insert(0, "src")

from kakao_cli import consolelock, fmp4, video, webui           # noqa: E402
from kakao_cli.errors import ConsoleBusy                          # noqa: E402
from kakao_cli.scrcpy import enc_reset_video, enc_resize_display, enc_start_app  # noqa: E402
from kakao_cli.screen import ScreenBuffer                         # noqa: E402

SPS = b"\x67\x42\xc0\x29\x8d\x68\x0a\x03\xda"
PPS = b"\x68\xce\x3c\x80"
SC = b"\x00\x00\x00\x01"
CONFIG = SC + SPS + SC + PPS
KEY = SC + b"\x09\xf0" + SC + SPS + SC + PPS + b"\x00\x00\x01\x65\x88\x84\x00\x10"
DELTA = SC + b"\x41\x9a\x02\x04"


# ------------------------------------------------------------------ fMP4
def test_split_annexb_and_avcc():
    nals = fmp4.split_annexb(KEY)
    assert [fmp4.nal_type(n) for n in nals] == [9, 7, 8, 5]
    assert fmp4.sps_pps(CONFIG) == (SPS, PPS)
    sample = fmp4.to_avcc(KEY)                       # AUD/SPS/PPS dropped, IDR kept
    n = struct.unpack(">I", sample[:4])[0]
    assert n == len(sample) - 4 and sample[4] == 0x65
    assert fmp4.split_annexb(b"\x41\x9a") == [b"\x41\x9a"]        # no start code


def test_init_segment_and_fragment_boxes():
    init = fmp4.init_segment(SPS, PPS, 928, 744)
    kinds = [k for _d, k, _s in fmp4.parse_boxes(init)]
    for k in ("ftyp", "moov", "mvhd", "trak", "tkhd", "mdia", "mdhd", "hdlr", "minf",
              "stbl", "stsd", "mvex", "trex"):
        assert k in kinds, k
    i = init.index(b"avcC")
    assert init[i + 4:i + 8] == bytes([1, 0x42, 0xC0, 0x29])     # profile/compat/level from SPS
    m = fmp4.Muxer()
    assert m.init(b"\x00\x00\x01\x41", 1, 1) is None             # no SPS/PPS → no init
    assert m.init(CONFIG, 928, 744)
    f1 = m.frame(1_000_000, True, KEY)
    f2 = m.frame(1_033_000, False, DELTA)
    assert m.frame(1_040_000, False, SC + SPS) is None             # nothing but parameter sets
    b1 = fmp4.parse_boxes(f1)
    assert [k for d, k, _ in b1 if d == 0] == ["moof", "mdat"]
    assert [k for _d, k, _ in b1].count("trun") == 1
    # trun data_offset points at the mdat payload; tfdt starts at 0 then grows
    moof_size = b1[0][2]
    j = f1.index(b"trun")
    off = struct.unpack(">i", f1[j + 12:j + 16])[0]
    assert off == moof_size + 8 and f1[off:off + 9] == b"\x00\x00\x00\x05\x65\x88\x84\x00\x10"
    tfdt = lambda f: struct.unpack(">Q", f[f.index(b"tfdt") + 8:f.index(b"tfdt") + 16])[0]
    dur = lambda f: struct.unpack(">I", f[f.index(b"trun") + 16:f.index(b"trun") + 20])[0]
    # contiguous timeline: each sample starts where the previous ended; a sample
    # lasts as long as the gap since the previous frame (clamped)
    assert tfdt(f1) == 0 and dur(f1) == fmp4.DEFAULT_DUR
    assert tfdt(f2) == fmp4.DEFAULT_DUR and dur(f2) == 33_000
    # sample flags: sync vs non-sync
    assert f1[j + 24:j + 28] == struct.pack(">I", 0x02000000)
    k = f2.index(b"trun")
    assert f2[k + 24:k + 28] == struct.pack(">I", 0x01010000)
    f3 = m.frame(9_000_000, False, DELTA)            # long idle gap: clamped, no jump
    assert tfdt(f3) == tfdt(f2) + dur(f2) and dur(f3) == fmp4.MAX_DUR
    f4 = m.frame(500_000, False, DELTA)              # pts going backwards: still monotonic
    assert tfdt(f4) == tfdt(f3) + dur(f3) and dur(f4) == fmp4.MIN_DUR


# ------------------------------------------------------------ geometry
def test_vd_geometry():
    assert video.vd_geometry(928, 744, 1) == (928, 744, 160)        # desktop: 1 dp ≈ 1 CSS px
    w, h, dpi = video.vd_geometry(390, 700, 2)                       # phone, dpr 2
    assert (w, h, dpi) == (784, 1400, 320) and w % 8 == 0
    w, h, dpi = video.vd_geometry(1920, 1200, 2)                     # too big: scaled ×0.5
    assert (w, h, dpi) == (1920, 1200, 160)
    assert video.vd_geometry(928, 744, 1, dpi_mode="device")[2] == round(340 * 744 / 1600)
    assert video.vd_geometry(928, 744, 1, dpi_mode="240")[2] == 240
    assert video.vd_geometry(100, 50, 1)[:2] == (320, 320)           # minimum side
    assert video.vd_geometry(928, 744, 0.25)[2] == 80                # dpr clamped to 0.5


# ----------------------------------------------------------------- hub
class _Dev:
    def __init__(self, lock_out=""):
        self.cmds = []
        self.lock_out = lock_out

    def shell(self, cmd):
        self.cmds.append(cmd)
        if cmd.startswith("cat " + consolelock.LOCK):
            return self.lock_out
        return ""


class _VBridge:
    """Fake ScrcpySession: records set_video/send, lets tests feed video events."""
    def __init__(self):
        self.on_video = self.on_connect = self.on_change = None
        self.connected, self.video = True, False
        self.calls, self.sent, self.taps, self.swipes, self.keys = [], [], [], [], []

    def set_video(self, enabled, **opts):
        self.video = enabled
        self.calls.append((enabled, opts))

    def send(self, data):
        self.sent.append(data)

    def tap(self, x, y, w, h):
        self.taps.append((x, y, w, h))

    def swipe(self, x1, y1, x2, y2, w, h, ms=200):
        self.swipes.append((x1, y1, x2, y2, w, h, ms))

    def key(self, kc):
        self.keys.append(kc)

    def clip(self):
        return 0, ""


def _drain(sub):
    out = []
    while not sub.q.empty():
        out.append(sub.q.get_nowait())
    return out


def test_hub_subscribe_fanout_and_key_gating():
    br, dev = _VBridge(), _Dev()
    hub = video.VideoHub(br, dev)
    s1 = hub.subscribe("fmp4", (928, 744, 1))
    assert br.calls[-1][0] is True
    opts = br.calls[-1][1]
    assert opts["new_display"] == "928x744/160" and opts["flex_display"] == "true"
    assert opts["vd_destroy_content"] == "false" and "max_size" not in opts
    assert opts["vd_system_decorations"] == "false" and opts["display_ime_policy"] == "local"
    br.on_connect()                                  # session up → KakaoTalk moved onto the VD
    assert br.sent[-1] == enc_start_app("com.kakao.talk")
    br.on_video(("session", 928, 744, 0))
    br.on_video(("config", CONFIG))
    br.on_video(("frame", 1, False, DELTA))          # before any keyframe: dropped
    br.on_video(("frame", 2, True, KEY))
    br.on_video(("frame", 3, False, DELTA))
    ev = _drain(s1)
    assert [e[0] for e in ev] == ["session", "config", "frame", "frame"]
    assert [e[1] for e in ev if e[0] == "frame"] == [2, 3]
    # a late viewer gets the cached session/config and a keyframe is requested
    n = len(br.calls)
    s2 = hub.subscribe("annexb", (500, 500, 1))
    assert len(br.calls) == n                        # no restart for the 2nd viewer
    assert [e[0] for e in _drain(s2)] == ["session", "config"]
    assert br.sent[-1] == enc_reset_video()
    br.on_video(("frame", 4, False, DELTA))
    assert _drain(s2) == [] and len(_drain(s1)) == 1
    info = hub.info()
    assert info["viewers"] == 2 and (info["w"], info["h"]) == (928, 744) and info["mode"] == "virtual"


def test_hub_slow_viewer_resyncs_on_keyframe():
    br = _VBridge()
    hub = video.VideoHub(br, _Dev())
    s = hub.subscribe("annexb", (928, 744, 1))
    br.on_video(("session", 928, 744, 0))
    br.on_video(("config", CONFIG))
    br.on_video(("frame", 0, True, KEY))
    for i in range(s.q.maxsize):                     # queue full of frames nobody reads
        br.on_video(("frame", i + 1, False, DELTA))
    assert s.need_key is True                        # overflow → flushed, wait for a key
    assert [e[0] for e in _drain(s)] == ["session", "config"]
    br.on_video(("frame", 999, False, DELTA))
    assert _drain(s) == []
    br.on_video(("frame", 1000, True, KEY))
    assert [e[1] for e in _drain(s)] == [1000]


def test_hub_grace_stop_clears_lock(monkeypatch):
    monkeypatch.setattr(video, "GRACE_S", 0.05)
    br, dev = _VBridge(), _Dev()
    hub = video.VideoHub(br, dev)
    s = hub.subscribe("fmp4", (928, 744, 1))
    hub.unsubscribe(s)
    s = hub.subscribe("fmp4", (928, 744, 1))         # rejoin within grace: no stop
    time.sleep(0.15)
    assert br.calls[-1][0] is True and hub.active
    hub.unsubscribe(s)
    time.sleep(0.2)
    assert br.calls[-1] == (False, {}) and not hub.active
    assert any("then rm -f " + consolelock.LOCK in c for c in dev.cmds)


def test_hub_resize_and_modes():
    br, dev = _VBridge(), _Dev()
    hub = video.VideoHub(br, dev)
    assert hub.resize(800, 600, 1)["applied"] is False             # not streaming yet
    hub.subscribe("fmp4", (928, 744, 1))
    n = len(br.calls)
    r = hub.resize(1000, 700, 1)                     # same dpi → RESIZE_DISPLAY (flex)
    assert r == {"w": 1000, "h": 704, "dpi": 160, "applied": True}
    assert br.sent[-1] == enc_resize_display(1000, 704) and len(br.calls) == n
    hub.resize(500, 700, 2)                          # dpi changes → restart with new_display
    assert len(br.calls) == n + 1 and br.calls[-1][1]["new_display"] == "912x1280/293"
    info = hub.set_mode("mirror")                    # manual toggle → mirror restart
    assert info["mode"] == "mirror" and "new_display" not in br.calls[-1][1]
    assert br.calls[-1][1]["max_size"] == 1280
    assert hub.resize(900, 700, 1) == {"mode": "mirror"}
    hub.set_mode("virtual")
    assert "new_display" in br.calls[-1][1]


def test_hub_falls_back_to_mirror_when_vd_never_streams():
    br, dev = _VBridge(), _Dev()
    hub = video.VideoHub(br, dev)
    hub.subscribe("fmp4", (928, 744, 1))
    hub._vd_deadline = time.time() - 1               # no session packet before the deadline
    for _ in range(30):
        if hub.mode == "mirror":
            break
        time.sleep(0.1)
    assert hub.mode == "mirror" and "미러" in hub.note
    assert "max_size" in br.calls[-1][1]


def test_hub_heartbeat_writes_lock_only_for_live_vd():
    br, dev = _VBridge(), _Dev()
    hub = video.VideoHub(br, dev)
    hub.subscribe("fmp4", (928, 744, 1))
    br.on_video(("session", 928, 744, 0))
    for _ in range(30):
        if any('echo "$now ' in c and consolelock.LOCK in c for c in dev.cmds):
            break
        time.sleep(0.1)
    w = [c for c in dev.cmds if 'echo "$now ' in c]
    assert w and ' virtual" > ' + consolelock.LOCK in w[0]


# ---------------------------------------------------------- console lock
def test_consolelock_parse_and_check():
    fresh, info = consolelock.parse("1700000000 vm 123 virtual\n==\n1700000005\n")
    assert fresh and info == {"host": "vm", "pid": "123", "mode": "virtual", "age_s": 5}
    assert consolelock.parse("1700000000 vm 1 virtual\n==\n1700000100")[0] is False   # stale
    assert consolelock.parse("==\n1700000000") == (False, None)                       # no lock
    assert consolelock.parse("") == (False, None)
    consolelock.check(_Dev("==\n1700000000"))                                          # free
    try:
        consolelock.check(_Dev("1700000000 vm 1 virtual\n==\n1700000003"))
        raise AssertionError("expected ConsoleBusy")
    except ConsoleBusy as e:
        assert e.code == "console_busy" and "웹 콘솔" in str(e)


def test_cli_refuses_ui_commands_while_console_busy(monkeypatch, capsys):
    from kakao_cli import cli, tui
    dev = _Dev("1700000000 vm 1 virtual\n==\n1700000002")
    monkeypatch.setattr(cli, "_dev", lambda args: dev)
    monkeypatch.setattr(tui, "run", lambda **kw: (_ for _ in ()).throw(AssertionError("tui started")))
    for argv in (["rooms", "--json"], ["read", "me", "--json"], ["send", "me", "x", "--yes", "--json"],
                 ["tui", "--json"]):
        rc = cli.main(argv)
        out = capsys.readouterr()
        payload = json.loads(out.out or out.err)
        assert rc == ConsoleBusy.exit_code == 7, argv
        assert payload["ok"] is False and payload["error"] == "console_busy" and payload["mode"] == "virtual"
    assert not any(c.startswith("input ") for c in dev.cmds)          # nothing touched the UI


# ------------------------------------------------- endpoints + routing
class _Worker:
    def __init__(self):
        import queue
        self.events = queue.Queue()
        self.puts = []

    def put(self, *cmd):
        self.puts.append(cmd)

    def put_wait(self, *cmd, **kwargs):
        if cmd[0] == "consolekey":
            cmd[1].key(cmd[2])
        else:
            self.put(*cmd)


def _png(w, h):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h)
            + b"\x08\x06\x00\x00\x00" + b"\x00" * 8)


def _server(bridge=None, hub=None):
    screen = ScreenBuffer()
    screen.set_frame(_png(1600, 2560))               # display 0 (PNG path)
    worker = _Worker()
    httpd = webui.serve(worker, "127.0.0.1", 0, screen=screen, bridge=bridge, hub=hub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, worker, httpd.server_address[1]


def _post(port, path, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_composition_endpoint_validates_and_uses_ordered_worker():
    httpd, worker, port = _server()
    try:
        body = {"session": "viewer:1", "phase": "update", "text": "한"}
        assert _post(port, "/api/screen/composition", body)[0] == 200
        assert worker.puts == [("composition", body)]
        assert _post(port, "/api/screen/composition", dict(body, text=["invalid"]))[0] == 400
        assert _post(port, "/api/screen/composition", dict(body, phase="delete"))[0] == 400
        assert len(worker.puts) == 1
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/screen/composition", json.dumps(body),
                  {"Content-Type": "application/json", "Origin": "http://evil.invalid"})
        response = c.getresponse()
        assert response.status == 403
        response.read(); c.close()
        assert len(worker.puts) == 1
        worker.put_wait = lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("fixture"))
        assert _post(port, "/api/screen/composition", body)[0] == 503
    finally:
        httpd.shutdown()


def _read_frame(r):
    t, n = struct.unpack(">BI", r.read(5))
    return t, r.read(n)


def test_video_stream_endpoint_fmp4_and_annexb():
    br = _VBridge()
    hub = video.VideoHub(br, _Dev())
    httpd, _w, port = _server(br, hub)
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/api/video/info")
        info = json.loads(c.getresponse().read())
        assert info["mode"] == "virtual" and info["active"] is False
        c.request("GET", "/api/video/stream?fmt=bogus")
        r = c.getresponse(); r.read()
        assert r.status == 400
        for fmt in ("fmp4", "annexb"):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", "/api/video/stream?fmt=%s&w=928&h=744&dpr=1" % fmt)
            r = c.getresponse()
            assert r.status == 200
            for _ in range(50):
                if hub.subs:
                    break
                time.sleep(0.02)
            br.on_video(("session", 928, 744, 0))
            br.on_video(("config", CONFIG))
            br.on_video(("frame", 10, False, DELTA))          # not a key: skipped
            br.on_video(("frame", 20, True, KEY))
            br.on_video(("frame", 30, False, DELTA))
            t, meta = _read_frame(r)
            meta = json.loads(meta)
            assert t == 1 and meta == {"w": 928, "h": 744, "mode": "virtual", "codec": "avc1.42C029"}
            t, init = _read_frame(r)
            assert t == 2
            if fmt == "fmp4":
                assert init[4:8] == b"ftyp"
            else:
                assert init == CONFIG
            t, m1 = _read_frame(r)
            if fmt == "fmp4":
                assert t == 4 and json.loads(m1) == {"key_time": 0}
                t, m1 = _read_frame(r)
            t2, m2 = _read_frame(r)
            assert t == t2 == 3
            if fmt == "fmp4":
                assert [k for d, k, _ in fmp4.parse_boxes(m1) if d == 0] == ["moof", "mdat"]
            else:
                assert m1[:9] == struct.pack(">BQ", 1, 20) and m1[9:] == KEY
                assert m2[:9] == struct.pack(">BQ", 0, 30)
            if fmt == "fmp4":
                # A keyframe's buffer timestamp follows the contiguous muxer
                # timeline, not the scrcpy clock (which may have a long gap).
                br.on_video(("frame", 60_000_000, True, KEY))
                t, key_meta = _read_frame(r)
                assert t == 4 and json.loads(key_meta) == {"key_time": 0.034333}
                assert _read_frame(r)[0] == 3
            r.close(); c.close()
            for _ in range(100):                     # closed tab noticed without any new frame
                if not hub.subs:
                    break
                time.sleep(0.05)
            assert not hub.subs
        st, j = _post(port, "/api/video/mode", {"mode": "nope"})
        assert st == 400
    finally:
        httpd.shutdown()


def test_video_unavailable_without_scrcpy():
    httpd, _w, port = _server()
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/api/video/info")
        assert json.loads(c.getresponse().read()) == {"available": False}
        c.request("GET", "/api/video/stream?fmt=fmp4")
        r = c.getresponse(); r.read()
        assert r.status == 503
    finally:
        httpd.shutdown()


def test_wc_coalesces_frames_and_releases_pending_resources():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to execute the WebCodecs regression")
    source = "function wcSink(){" + webui.PAGE.split("function wcSink(){", 1)[1].split("let lastVideoKey", 1)[0]
    test = Path(__file__).with_name("webui_wc_frames.js")
    result = subprocess.run([node, str(test)], input=source, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mse_buffer_retains_keyframes_with_sparse_encoder_output():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to execute the MSE buffer regression")
    source = "function mseSink(){" + webui.PAGE.split("function mseSink(){", 1)[1].split("// flex:", 1)[0]
    test = Path(__file__).with_name("webui_mse_buffer.js")
    result = subprocess.run([node, str(test)], input=source, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_input_routes_to_the_display_the_viewer_sees():
    br = _VBridge()
    hub = video.VideoHub(br, _Dev())
    httpd, worker, port = _server(br, hub)
    try:
        hub.subscribe("fmp4", (928, 744, 1))
        br.on_video(("session", 928, 744, 0))
        # video viewer: element 464x372 shows the 928x744 stream → scrcpy, VD coords
        st, j = _post(port, "/api/screen/tap", {"cx": 232, "cy": 186, "ew": 464, "eh": 372,
                                                "iw": 928, "ih": 744, "src": "video"})
        assert st == 200 and j["via"] == "scrcpy" and br.taps[-1] == (464, 372, 928, 744)
        from kakao_cli.scrcpy import enc_secondary_click
        point = {"cx": 232, "cy": 186, "ew": 464, "eh": 372, "iw": 928, "ih": 744, "src": "video"}
        assert _post(port, "/api/screen/rightclick", point)[0] == 200
        assert br.sent[-1] == enc_secondary_click(464, 372, 928, 744)
        before = len(br.sent)
        assert _post(port, "/api/screen/rightclick", dict(point, src="png"))[0] == 409
        assert len(br.sent) == before
        st, j = _post(port, "/api/screen/swipe", {"cx1": 0, "cy1": 0, "cx2": 463, "cy2": 371,
                                                  "ew": 464, "eh": 372, "iw": 928, "ih": 744, "src": "video"})
        assert j["via"] == "scrcpy" and br.swipes[-1][:6] == (0, 0, 926, 742, 928, 744)
        assert _post(port, "/api/screen/key", {"name": "back", "src": "video"})[1]["via"] == "scrcpy"
        assert br.keys == [4]
        # PNG viewer (display 0) while a VD is up: adb on display 0, PNG coordinates
        st, j = _post(port, "/api/screen/tap", {"cx": 400, "cy": 640, "ew": 800, "eh": 1280,
                                                "iw": 1600, "ih": 2560, "src": "png"})
        assert j["via"] == "adb" and worker.puts[-1] == ("tap", 800, 1280)
        assert _post(port, "/api/screen/key", {"name": "home", "src": "png"})[1]["via"] == "adb"
        assert worker.puts[-1] == ("key", "home") and br.keys == [4]
        # mirror mode / no VD: keys may use scrcpy (it injects into display 0)
        hub.set_mode("mirror")
        assert _post(port, "/api/screen/key", {"name": "enter", "src": "png"})[1]["via"] == "scrcpy"
        assert _post(port, "/api/screen/key", {"code": 999999})[0] == 400
    finally:
        httpd.shutdown()


def test_live_pointer_wheel_routing_and_expiry(monkeypatch):
    from kakao_cli.scrcpy import enc_touch, enc_scroll, enc_cancel_touch, ACTION_DOWN, ACTION_MOVE, ACTION_CANCEL
    monkeypatch.setattr(webui.PointerInput, 'LEASE_S', 0.08)
    br = _VBridge()
    hub = video.VideoHub(br, _Dev())
    httpd, _worker, port = _server(br, hub)
    try:
        hub.subscribe('annexb', (928, 744, 1))
        br.on_video(('session', 928, 744, 0))
        body = dict(cx=232, cy=186, ew=464, eh=372, iw=928, ih=744, src='video', gesture='one')
        assert _post(port, '/api/screen/touch', dict(body, action='down'))[0] == 200
        assert br.sent[-1] == enc_touch(ACTION_DOWN, 464, 372, 928, 744)
        assert _post(port, '/api/screen/touch', dict(body, action='move', gesture='other'))[0] == 409
        assert _post(port, '/api/screen/touch', dict(body, action='move'))[0] == 200
        assert br.sent[-1] == enc_touch(ACTION_MOVE, 464, 372, 928, 744)
        for _ in range(30):
            if br.sent[-1][1] == ACTION_CANCEL:
                break
            time.sleep(0.01)
        assert br.sent[-1] == enc_cancel_touch(464, 372, 928, 744)
        assert br.sent[-1][33] == 1, 'UP must clear scrcpy pointer bookkeeping after Android CANCEL'
        assert _post(port, '/api/screen/touch', dict(body, action='up'))[0] == 409
        assert _post(port, '/api/screen/touch', dict(body, action=[]))[0] == 400
        assert _post(port, '/api/screen/touch', dict(body, src='png', action='down'))[0] == 409
        assert _post(port, '/api/screen/scroll', dict(body, dx=0.5, dy=-1))[0] == 200
        assert br.sent[-1] == enc_scroll(464, 372, 928, 744, 0, -1)
        # Golden protocol values: one notch is 1/16 of the signed fixed point range.
        assert struct.unpack('>BiiHHhhI', br.sent[-1]) == (3, 464, 372, 928, 744, 0, -2048, 0)
        before = len(br.sent)
        assert _post(port, '/api/screen/scroll', dict(body, dx=1, dy=0))[0] == 200
        assert len(br.sent) == before, 'horizontal-only scroll must never be injected'
    finally:
        httpd.shutdown()


def test_pointer_resize_or_reconnect_cancels_old_gesture():
    from kakao_cli.scrcpy import ACTION_DOWN, ACTION_MOVE
    import pytest
    br = _VBridge()
    p = webui.PointerInput()
    p.send(br, 'one', ACTION_DOWN, 10, 20, 928, 744)
    with pytest.raises(ValueError, match='display changed'):
        p.send(br, 'one', ACTION_MOVE, 10, 20, 640, 480)
    br.scid = 'old'
    p.send(br, 'two', ACTION_DOWN, 10, 20, 928, 744)
    br.scid = 'new'
    before = len(br.sent)
    with pytest.raises(ValueError, match='display changed'):
        p.send(br, 'two', ACTION_MOVE, 10, 20, 928, 744)
    assert len(br.sent) == before, 'never inject an old gesture into a new scrcpy session'


def test_quality_preserves_logical_size_and_is_shared():
    br = _VBridge()
    hub = video.VideoHub(br, _Dev())
    sub = hub.subscribe('annexb', (464, 864, 2))
    w, h, dpi = hub.vd
    assert h == 1280 and abs(w * 160 / dpi - 464) < 5
    assert hub.info()['max_fps'] == 60
    hub.set_quality(960)
    w, h, dpi = hub.vd
    assert h == 960 and abs(w * 160 / dpi - 464) < 5
    assert hub.info()['max_side'] == 960
    hub.subscribe('annexb', (800, 600, 1))
    assert hub.vd == (w, h, dpi), 'a second viewer must not change the first viewer geometry'
    hub.unsubscribe(sub)


def test_age_budget_resyncs_before_frame_count_limit(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(video.time, 'monotonic', lambda: now[0])
    br = _VBridge()
    hub = video.VideoHub(br, _Dev())
    sub = hub.subscribe('annexb', (928, 744, 1))
    br.on_video(('session', 928, 744, 0))
    br.on_video(('config', CONFIG))
    br.on_video(('frame', 0, True, KEY))
    now[0] = video.FRAME_AGE_S + 0.01
    br.on_video(('frame', 1, False, DELTA))
    assert [e[0] for e in _drain(sub)] == ['session', 'config']
    assert sub.need_key and br.sent[-1] == enc_reset_video()
    br.on_video(('frame', 2, True, KEY))
    assert _drain(sub) == [('frame', 2, True, KEY)]


def test_browser_live_input_and_keyboard_latency():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    result = subprocess.run([node, str(Path(__file__).with_name('webui_input_latency.js'))],
                            input=webui.PAGE, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_worker_text_completion_precedes_key_and_surfaces_failure():
    from kakao_cli.worker import Worker
    from types import SimpleNamespace
    import pytest
    order = []
    dev = SimpleNamespace()
    def text(t):
        time.sleep(0.03)
        if t == 'fail':
            raise RuntimeError('IME unavailable')
        order.append(('text', t))
    dev.type_text = text
    w = Worker(dev, chat=False)
    br = _VBridge()
    br.key = lambda kc: order.append(('key', kc))
    w.start()
    try:
        w.put_wait('screentext', '한글')
        w.put_wait('consolekey', br, 66)
        assert order == [('text', '한글'), ('key', 66)]
        with pytest.raises(RuntimeError, match='IME unavailable'):
            w.put_wait('screentext', 'fail')
        w.put_wait('screentext', '복구')
        assert order[-1] == ('text', '복구')
    finally:
        w.put('quit')

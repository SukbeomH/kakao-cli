import json
import os
import urllib.parse
import subprocess
import sys


def run(args):
    return subprocess.run([sys.executable, "-m", "kakao_cli", *args],
                          capture_output=True, text=True, cwd="src")


def test_version():
    r = run(["--version"])
    assert r.returncode == 0
    assert "kakao-cli" in r.stdout


def test_help_lists_commands():
    r = run(["--help"])
    assert r.returncode == 0
    for cmd in ("doctor", "rooms", "send", "send-file", "poll", "pull-media"):
        assert cmd in r.stdout


def test_subcommand_help():
    r = run(["send", "--help"])
    assert r.returncode == 0
    assert "--yes" in r.stdout and "room" in r.stdout


def test_doctor_no_device_is_structured_error(tmp_path):
    # The developer may have a real device connected; never inspect it in this test.
    fake_adb = tmp_path / "adb"
    fake_adb.write_text('#!/bin/sh\n[ "$1" = devices ] || exit 99\nprintf "List of devices attached\\n\\n"\n')
    fake_adb.chmod(0o755)
    r = run(["doctor", "--json", "--adb", str(fake_adb)])
    assert r.returncode == 4
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "device_not_found"


def test_send_requires_positional_args():
    # argparse should reject missing room/text with exit code 2.
    r = run(["send"])
    assert r.returncode == 2


def test_webui_help():
    r = run(["webui", "--help"])
    assert r.returncode == 0
    assert "--host" in r.stdout and "--port" in r.stdout


# --- webui server (stub worker, no device) ---
import json as _json
import queue
import time
import urllib.request


class _StubWorker:
    """Stands in for Worker: records commands, exposes an events queue."""
    def __init__(self):
        self.events = queue.Queue()
        self.puts = []

    def put(self, *cmd):
        self.puts.append(cmd)

    def put_wait(self, *cmd):
        self.put(*cmd)


def _http(method, url, body=None):
    data = _json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _wait_room(url, want, tries=50):
    for _ in range(tries):
        _c, b = _http("GET", url + "/api/state")
        if _json.loads(b).get("room") == want:
            return
        time.sleep(0.05)
    raise AssertionError("room never opened in state")


# --- live device screen (stage C) ---
import struct


def _png(w, h):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h)
            + b"\x08\x06\x00\x00\x00" + b"\x00" * 8)


def test_webui_console_page_and_state(tmp_path):
    """The web UI is console-only: no chat endpoints, a status-only /api/state."""
    import threading
    sys.path.insert(0, "src")
    from kakao_cli import webui
    worker = _StubWorker()
    httpd = webui.serve(worker, "127.0.0.1", 0, fs_root=str(tmp_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st, body = _http("GET", base + "/")
        assert st == 200 and "kakao-cli" in body and 'id="conwrap"' in body
        assert 'id="list"' not in body and "/api/send" not in body     # no chat UI
        st, body = _http("GET", base + "/api/state")
        snap = _json.loads(body)
        assert st == 200 and set(snap) == {"seq", "status", "clip_seq", "scrcpy"}
        worker.events.put(("status", "hello"))
        for _ in range(50):
            snap = _json.loads(_http("GET", base + "/api/state")[1])
            if snap["status"] == "hello":
                break
            time.sleep(0.05)
        assert snap["status"] == "hello"
        assert _http("GET", base + "/api/state?since=%d" % snap["seq"])[0] == 204
        for path in ("/api/open", "/api/send", "/api/export", "/api/rooms", "/api/sendfile"):
            assert _http("POST", base + path, {"room": "me", "text": "x"})[0] == 404
    finally:
        httpd.shutdown()


def test_screen_view_to_device():
    sys.path.insert(0, "src")
    from kakao_cli.screen import view_to_device
    # element matches the device aspect (no letterbox): proportional mapping
    assert view_to_device(200, 320, 400, 640, 1600, 2560) == (800, 1280)
    assert view_to_device(0, 0, 400, 640, 1600, 2560) == (0, 0)
    assert view_to_device(400, 640, 400, 640, 1600, 2560) == (1599, 2559)  # clamped
    # wide element → horizontal letterbox (offset 200px each side)
    assert view_to_device(400, 320, 800, 640, 1600, 2560) == (800, 1280)
    assert view_to_device(200, 0, 800, 640, 1600, 2560) == (0, 0)
    # degenerate inputs are safe
    assert view_to_device(10, 10, 0, 0, 1600, 2560) == (0, 0)
    # same click on the scaled video frame (max_size 1280 → 800×1280)
    assert view_to_device(200, 320, 400, 640, 800, 1280) == (400, 640)


def test_messages_detailed_sender_mine_date():
    sys.path.insert(0, "src")
    from kakao_cli.driver import Driver
    from kakao_cli.ui import Screen
    P = "com.kakao.talk:id"
    xml = (
        '<hierarchy><node bounds="[0,0][1600,2560]">'
        f'<node resource-id="{P}/chat_log_recycler_list" bounds="[0,100][1600,2400]">'
        f'<node bounds="[0,100][1600,160]"><node resource-id="{P}/time_line" '
        'text="2026-10-17" bounds="[700,110][900,150]"/></node>'
        '<node bounds="[0,160][1600,300]">'
        f'<node resource-id="{P}/nickname" text="Alice" bounds="[200,170][400,210]"/>'
        f'<node resource-id="{P}/message_container" bounds="[200,210][900,290]">'
        f'<node resource-id="{P}/message" text="hi" bounds="[210,220][890,280]"/></node></node>'
        '<node bounds="[0,300][1600,440]">'
        f'<node resource-id="{P}/message_container" bounds="[1000,310][1569,390]">'
        f'<node resource-id="{P}/message" text="yo" bounds="[1010,320][1560,380]"/></node></node>'
        '<node bounds="[0,440][1600,560]">'
        f'<node resource-id="{P}/message_container" bounds="[200,450][700,530]">'
        f'<node resource-id="{P}/message" text="again" bounds="[210,460][690,520]"/></node></node>'
        '</node></node></hierarchy>'
    )
    msgs = Driver(None)._messages_detailed(Screen(xml))
    kinds = [(m["kind"], m.get("text"), m.get("sender"), m.get("is_mine")) for m in msgs]
    assert kinds[0] == ("date", "2026-10-17", None, None)
    assert kinds[1] == ("text", "hi", "Alice", False)      # incoming, left-aligned
    assert kinds[2] == ("text", "yo", "", True)            # mine, hugs right margin
    assert kinds[3] == ("text", "again", "", False)        # consecutive incoming, left
    # the text wrapper stays backward-compatible (strings only)
    assert Driver(None)._messages_on_screen(Screen(xml)) == ["2026-10-17", "hi", "yo", "again"]


def test_screen_login_detection():
    sys.path.insert(0, "src")
    from kakao_cli.screen import is_login_screen
    assert is_login_screen("com.kakao.talk/.activity.authenticator.AuthenticatorActivity")
    assert is_login_screen("com.kakao.talk/.activity.main.MainActivity",
                           '<node resource-id="com.kakao.talk:id/signup_kakao_account_with_qr"/>')
    assert not is_login_screen("com.kakao.talk/.activity.main.MainActivity", "<node/>")
    assert not is_login_screen("", "")


def test_screen_png_size_and_buffer():
    sys.path.insert(0, "src")
    from kakao_cli.screen import png_size, ScreenBuffer
    assert png_size(_png(1600, 2560)) == (1600, 2560)
    assert png_size(b"not a png") is None
    buf = ScreenBuffer(ttl=0.2)
    assert buf.active() is False          # nobody watching yet
    buf.touch()
    assert buf.active() is True
    buf.set_frame(_png(1080, 1920))
    seq, frame, w, h = buf.get()
    assert (w, h) == (1080, 1920) and frame and seq == 1


def test_webui_screen_disabled_blocks(tmp_path):
    import threading
    sys.path.insert(0, "src")
    from kakao_cli import webui
    worker = _StubWorker()
    httpd = webui.serve(worker, "127.0.0.1", 0, screen_enabled=False)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        c, _ = _http("GET", base + "/api/screen/frame")
        assert c == 403
        c, _ = _http("POST", base + "/api/screen/tap", {"cx": 1, "cy": 1, "ew": 10, "eh": 10})
        assert c == 403
        # info still reports (enabled: false) without leaking frames
        c, b = _http("GET", base + "/api/screen/info")
        assert c == 200 and _json.loads(b)["enabled"] is False
    finally:
        httpd.shutdown()


def test_webui_screen_control(tmp_path):
    import threading
    import urllib.request
    sys.path.insert(0, "src")
    from kakao_cli import webui
    from kakao_cli.screen import ScreenBuffer
    worker = _StubWorker()
    screen = ScreenBuffer()
    screen.set_frame(_png(1600, 2560))
    httpd = webui.serve(worker, "127.0.0.1", 0, screen=screen, screen_enabled=True)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        # H.264 startup only checks capability; PNG polling still acquires a lease.
        c, b = _http("GET", base + "/api/screen/info?peek=1")
        assert c == 200 and _json.loads(b)["has_frame"] is True
        assert screen.active() is False
        c, _ = _http("GET", base + "/api/screen/info")
        assert c == 200 and screen.active() is True
        # tap maps element coords → device coords and enqueues a worker command
        c, b = _http("POST", base + "/api/screen/tap", {"cx": 200, "cy": 320, "ew": 400, "eh": 640})
        assert c == 200
        assert ("tap", 800, 1280) in worker.puts
        # bad key rejected
        c, _ = _http("POST", base + "/api/screen/key", {"name": "power"})
        assert c == 400
        # good key enqueued
        c, _ = _http("POST", base + "/api/screen/key", {"name": "back"})
        assert c == 200 and ("key", "back") in worker.puts
        # non-JSON content-type rejected (CSRF hardening)
        req = urllib.request.Request(base + "/api/screen/tap", data=b"cx=1",
                                     method="POST", headers={"Content-Type": "text/plain"})
        try:
            urllib.request.urlopen(req, timeout=5); code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 415
    finally:
        httpd.shutdown()


# --- console: file manager + keyboard passthrough ---
def test_fs_parse_and_quote():
    sys.path.insert(0, "src")
    from kakao_cli.fs import parse_stat_lines, shq, android_parent, unique_name
    out = ("regular file|12|100|/sdcard/a b|c.txt\n"
           "directory|4096|200|/sdcard/Download\n"
           "find: '/sdcard/x': Permission denied\n"
           "junk line\n")
    items = parse_stat_lines(out)
    assert [i["name"] for i in items] == ["Download", "a b|c.txt"]   # dirs first, '|' in name kept
    assert items[0]["type"] == "dir" and items[1]["size"] == 12
    assert shq("it's") == "'it'\\''s'"
    assert android_parent("/sdcard/Download/") == "/sdcard/"
    assert android_parent("/sdcard") == "/"
    taken = {"a.txt", "a-1.txt"}
    assert unique_name("a.txt", taken.__contains__) == "a-2.txt"
    assert unique_name("b.txt", taken.__contains__) == "b.txt"


def test_fs_local_root_containment(tmp_path):
    import os
    sys.path.insert(0, "src")
    from kakao_cli.fs import LocalRoot
    root = tmp_path / "root"; (root / "sub").mkdir(parents=True)
    (root / "f.txt").write_text("x")
    outside = tmp_path / "secret"; outside.mkdir()
    os.symlink(outside, root / "escape")              # symlink pointing outside
    lr = LocalRoot(str(root))
    assert lr.resolve("sub") == str((root / "sub").resolve())
    assert lr.resolve("../secret") is None
    assert lr.resolve("escape") is None
    assert lr.resolve(str(outside)) is None
    names = [i["name"] for i in lr.list("")]
    assert names == ["sub", "f.txt"]                   # escape symlink hidden
    assert lr.list("../") is None


class _FakeDev:
    def __init__(self, files):
        self.files = dict(files)          # android path -> bytes
        self.pushed = []
        self.cmds = []

    def shell(self, cmd):
        self.cmds.append(cmd)
        if cmd.startswith("find "):
            return ("directory|4096|1|/sdcard/Download\n"
                    "regular file|3|2|/sdcard/hello.txt\n")
        if cmd.startswith("test -d"):
            return ""
        if cmd.startswith("test -e"):
            return "Y" if any(p in cmd for p in self.files) else ""
        return ""

    def pull(self, src, dst):
        with open(dst, "wb") as f:
            f.write(self.files[src])

    def push(self, src, dst):
        self.pushed.append((src, dst))
        self.files[dst] = open(src, "rb").read()


def _console_server(tmp_path, enabled=True, fake=None):
    import threading, types
    sys.path.insert(0, "src")
    from kakao_cli import webui
    worker = _StubWorker()
    worker.d = types.SimpleNamespace(dev=fake)
    httpd = webui.serve(worker, "127.0.0.1", 0, screen_enabled=enabled, fs_root=str(tmp_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, worker, f"http://127.0.0.1:{httpd.server_address[1]}"


def _wait_job(base, jid):
    for _ in range(100):
        c, b = _http("GET", base + "/api/fs/job/" + jid)
        j = _json.loads(b)
        if j.get("state") in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_console_disabled_blocks(tmp_path):
    httpd, _w, base = _console_server(tmp_path, enabled=False, fake=_FakeDev({}))
    try:
        assert _http("GET", base + "/api/fs/local")[0] == 403
        assert _http("GET", base + "/api/fs/android")[0] == 403
        assert _http("POST", base + "/api/fs/pull", {"src": "/sdcard/x", "dst": ""})[0] == 403
    finally:
        httpd.shutdown()


def test_console_listing_and_copy(tmp_path):
    (tmp_path / "up.txt").write_bytes(b"UPLOAD")
    (tmp_path / "hello.txt").write_bytes(b"old")       # forces a non-overwriting name
    fake = _FakeDev({"/sdcard/hello.txt": b"HEY", "/sdcard/Download/up.txt": b"taken"})
    httpd, worker, base = _console_server(tmp_path, fake=fake)
    try:
        c, b = _http("GET", base + "/api/fs/android?path=/sdcard")
        j = _json.loads(b)
        assert c == 200 and j["path"] == "/sdcard/" and j["parent"] == "/"
        assert [i["name"] for i in j["items"]] == ["Download", "hello.txt"]
        c, b = _http("GET", base + "/api/fs/local")
        assert c == 200 and "up.txt" in [i["name"] for i in _json.loads(b)["items"]]
        assert _http("GET", base + "/api/fs/local?path=../")[0] == 404
        # pull android → local root: existing hello.txt is NOT overwritten
        c, b = _http("POST", base + "/api/fs/pull", {"src": "/sdcard/hello.txt", "dst": ""})
        assert c == 202
        j = _wait_job(base, _json.loads(b)["id"])
        assert j["state"] == "done" and j["name"] == "hello-1.txt"
        assert (tmp_path / "hello-1.txt").read_bytes() == b"HEY"
        assert (tmp_path / "hello.txt").read_bytes() == b"old"
        # push local → android dir: taken name gets a suffix
        c, b = _http("POST", base + "/api/fs/push", {"src": "up.txt", "dst": "/sdcard/Download"})
        j = _wait_job(base, _json.loads(b)["id"])
        assert j["state"] == "done" and j["dst"] == "/sdcard/Download/up-1.txt"
        # escapes are refused up front
        assert _http("POST", base + "/api/fs/push", {"src": "../x", "dst": "/sdcard/"})[0] == 400
        assert _http("POST", base + "/api/fs/pull", {"src": "rel", "dst": ""})[0] == 400
    finally:
        httpd.shutdown()


def test_console_keycode_allowlist(tmp_path):
    sys.path.insert(0, "src")
    from kakao_cli.screen import ScreenBuffer
    httpd, worker, base = _console_server(tmp_path, fake=_FakeDev({}))
    try:
        assert _http("POST", base + "/api/screen/key", {"code": 67})[0] == 200
        assert ("key", 67) in worker.puts
        assert _http("POST", base + "/api/screen/key", {"code": 26})[0] == 400   # POWER: not allowed
        assert _http("POST", base + "/api/screen/key", {"code": True})[0] == 400
    finally:
        httpd.shutdown()


# --- console A-1/A-2: browser <-> VM/device transfers ---
import http.client
import io


def _req(base, method, path, body=b"", headers=None):
    host, port = base.split("//", 1)[1].split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    out = (r.status, data, dict(r.getheaders()))
    c.close()
    return out


UP = {"X-Kakao-Op": "upload", "Content-Type": "application/octet-stream"}


def test_fs_host_and_names():
    sys.path.insert(0, "src")
    from kakao_cli.fs import host_allowed, clean_upload_name, content_disposition
    assert host_allowed("192.168.0.30:8808") and host_allowed("127.0.0.1")
    assert host_allowed("[::1]:8800") and host_allowed("localhost:8800")
    assert not host_allowed("evil.example:8808")            # DNS-rebinding name
    assert host_allowed("box.tail1234.ts.net", ["box.tail1234.ts.net"])
    assert not host_allowed("")
    assert clean_upload_name("../../etc/보고서 최종.pdf") == "보고서 최종.pdf"
    assert clean_upload_name("a\\b\\c.txt") == "c.txt"
    assert clean_upload_name("..") is None and clean_upload_name("") is None
    cd = content_disposition("보고서 최종.pdf")
    assert "filename*=UTF-8''%EB%B3%B4" in cd and cd.startswith("attachment;")


def test_upload_and_download_local(tmp_path):
    import hashlib
    httpd, _w, base = _console_server(tmp_path, fake=_FakeDev({}))
    try:
        body = os.urandom(300_000)
        q = "/api/fs/upload?dir=&name=" + urllib.parse.quote("보고서 최종.bin")
        st, b, _ = _req(base, "POST", q, body, UP)
        assert st == 201 and _json.loads(b)["name"] == "보고서 최종.bin"
        assert (tmp_path / "보고서 최종.bin").read_bytes() == body
        st, b, _ = _req(base, "POST", q, b"second", UP)              # same name → -1
        assert st == 201 and _json.loads(b)["name"] == "보고서 최종-1.bin"
        assert not [p for p in os.listdir(tmp_path) if p.endswith(".part")]
        # download streams the exact bytes with safe headers
        st, data, h = _req(base, "GET", "/api/fs/local/get?path=" + urllib.parse.quote("보고서 최종.bin"))
        assert st == 200 and hashlib.sha256(data).digest() == hashlib.sha256(body).digest()
        assert h["Content-Type"] == "application/octet-stream"
        assert h["X-Content-Type-Options"] == "nosniff"
        assert "filename*=UTF-8''" in h["Content-Disposition"]
        assert _req(base, "GET", "/api/fs/local/get?path=../x")[0] == 404
    finally:
        httpd.shutdown()


def test_upload_rejections(tmp_path):
    import socket
    sys.path.insert(0, "src")
    from kakao_cli import webui
    import threading, types
    worker = _StubWorker(); worker.d = types.SimpleNamespace(dev=_FakeDev({}))
    httpd = webui.serve(worker, "127.0.0.1", 0, screen_enabled=True, fs_root=str(tmp_path),
                        max_upload=1000, disk_free=lambda d: 10 ** 12)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    q = "/api/fs/upload?dir=&name=a.txt"
    try:
        assert _req(base, "POST", q, b"x", {"Content-Type": "application/octet-stream"})[0] == 403
        assert _req(base, "POST", q, b"x", {**UP, "Origin": "http://evil.example"})[0] == 403
        assert _req(base, "POST", q, b"x", {**UP, "Host": "evil.example"})[0] == 403
        assert _req(base, "POST", q, b"x", {**UP, "Content-Type": "multipart/form-data; b=1"})[0] == 415
        assert _req(base, "POST", q, b"x" * 1001, UP)[0] == 413
        assert _req(base, "POST", "/api/fs/upload?dir=../&name=a.txt", b"x", UP)[0] == 404
        assert _req(base, "POST", "/api/fs/upload?dir=&name=..", b"x", UP)[0] == 400
        # interrupted body: promised 500 bytes, sent 100, then hung up
        s = socket.create_connection(("127.0.0.1", httpd.server_address[1]))
        s.sendall(("POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kakao-Op: upload\r\n"
                   "Content-Type: application/octet-stream\r\nContent-Length: 500\r\n\r\n" % q).encode()
                  + b"y" * 100)
        s.shutdown(socket.SHUT_WR); s.recv(4096); s.close()
        time.sleep(0.2)
        assert not (tmp_path / "a.txt").exists()
        assert not [p for p in os.listdir(tmp_path) if p.endswith(".part")]
    finally:
        httpd.shutdown()
    # no free space → 507
    httpd = webui.serve(worker, "127.0.0.1", 0, screen_enabled=True, fs_root=str(tmp_path),
                        disk_free=lambda d: 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert _req(base, "POST", q, b"x", UP)[0] == 507
    finally:
        httpd.shutdown()


class _FakeDev2(_FakeDev):
    def shell(self, cmd):
        if cmd.startswith("stat -c '%F|%s'"):
            path = cmd.split("'")[3] if cmd.count("'") >= 4 else ""
            for p, b in self.files.items():
                if p == path:
                    return "regular file|%d" % len(b)
            return ""
        return super().shell(cmd)

    def open_exec_out(self, cmd):
        path = cmd.split("'", 1)[1].rsplit("'", 1)[0]
        return types_ns(stdout=io.BytesIO(self.files[path]), kill=lambda: None)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def test_android_upload_and_download(tmp_path):
    fake = _FakeDev2({"/sdcard/Download/pic.jpg": b"\xff\xd8\xffJPEGDATA"})
    httpd, _w, base = _console_server(tmp_path, fake=fake)
    try:
        st, b, _ = _req(base, "POST", "/api/fs/upload?target=android&dir=/sdcard/Download/&name=up.bin",
                        b"PAYLOAD", UP)
        assert st == 202
        j = _wait_job(base, _json.loads(b)["id"])
        assert j["state"] == "done" and j["dst"] == "/sdcard/Download/up.bin"
        assert fake.files["/sdcard/Download/up.bin"] == b"PAYLOAD"
        assert fake.pushed and not os.path.exists(fake.pushed[-1][0])   # staging removed
        st, data, h = _req(base, "GET", "/api/fs/android/get?path=/sdcard/Download/pic.jpg")
        assert st == 200 and data == b"\xff\xd8\xffJPEGDATA" and h["Content-Length"] == "11"
        assert _req(base, "GET", "/api/fs/android/get?path=/sdcard/nope")[0] == 404
        assert _req(base, "GET", "/api/fs/android/get?path=rel")[0] == 400
    finally:
        httpd.shutdown()


# --- console B: scrcpy bridge (protocol, pinning, reconnect, clipboard API) ---
def test_scrcpy_control_encoding():
    import struct
    sys.path.insert(0, "src")
    from kakao_cli import scrcpy as S
    k = S.enc_keycode(S.ACTION_DOWN, 67)
    assert len(k) == 14 and k[0] == 0 and struct.unpack(">BBiii", k) == (0, 0, 67, 0, 0)
    t = S.enc_touch(S.ACTION_DOWN, 400, 640, 800, 1280)
    assert len(t) == 32
    typ, act, pid, x, y, w, h, pr, ab, bt = struct.unpack(">BBqiiHHHii", t)
    assert (typ, act, pid, x, y, w, h, pr) == (2, 0, -2, 400, 640, 800, 1280, 0xFFFF)
    assert struct.unpack(">BBqiiHHHii", S.enc_touch(S.ACTION_UP, 1, 2, 3, 4))[7] == 0
    c = S.enc_set_clipboard(7, "한글🙂", True)
    assert c[:14] == struct.pack(">BqBi", 9, 7, 1, len("한글🙂".encode())) and c[14:].decode() == "한글🙂"
    try:
        S.enc_set_clipboard(1, "x" * (S.CLIP_MAX + 1), False); assert False
    except ValueError:
        pass
    assert S.enc_reset_video() == b"\x11"
    assert S.enc_get_clipboard() == b"\x08\x00"
    mouse = S.enc_secondary_click(123, 456, 720, 1280)
    down, up = (struct.unpack(">BBqiiHHHii", mouse[i:i+32]) for i in (0, 32))
    assert down == (2, 0, -1, 123, 456, 720, 1280, 65535, 2, 2)
    assert up == (2, 1, -1, 123, 456, 720, 1280, 0, 2, 0)
    assert S.enc_start_app("com.kakao.talk") == b"\x10\x0e" + b"com.kakao.talk"
    assert S.enc_resize_display(1280, 800) == struct.pack(">BHH", 21, 1280, 800)
    assert S.enc_scan_file("/sdcard/a") == struct.pack(">Bi", 22, 9) + b"/sdcard/a"


def test_scrcpy_device_and_video_parsers():
    import struct
    sys.path.insert(0, "src")
    from kakao_cli import scrcpy as S
    msg = (b"\x00" + struct.pack(">i", 6) + "복사".encode() + b"\x01" + struct.pack(">q", 42)
           + b"\x02" + struct.pack(">HH", 1, 2) + b"zz")
    p = S.DeviceMsgParser(); out = []
    for i in range(len(msg)):                       # byte-by-byte: framing survives splits
        out += p.feed(msg[i:i + 1])
    assert out == [("clipboard", "복사"), ("ack", 42)]
    sps = b"\x00\x00\x00\x01\x67\x42\xc0\x29\x8d"
    stream = (struct.pack(">I", 0x68323634) + struct.pack(">III", 0x80000000, 800, 1280)
              + struct.pack(">QI", 1 << 62, len(sps)) + sps
              + struct.pack(">QI", (1 << 61) | 1000, 3) + b"KEY"
              + struct.pack(">QI", 2000, 2) + b"DD")
    v = S.VideoParser(); ev = []
    for i in range(0, len(stream), 5):
        ev += v.feed(stream[i:i + 5])
    assert ev[0] == ("codec", 0x68323634) and ev[1] == ("session", 800, 1280, 0)
    assert ev[2] == ("config", sps) and ev[3] == ("frame", 1000, True, b"KEY")
    assert ev[4] == ("frame", 2000, False, b"DD")
    assert S.avc_codec_string(sps) == "avc1.42C029"


def test_scrcpy_server_pinning(tmp_path):
    sys.path.insert(0, "src")
    from kakao_cli import scrcpy as S
    good = tmp_path / "good"; good.write_bytes(b"pinned")
    old = S.SHA256
    S.SHA256 = S.sha256_file(str(good))
    try:
        assert S.ensure_server(str(good)) == str(good)
        bad = tmp_path / "bad"; bad.write_bytes(b"tampered")
        for f in (lambda: S.ensure_server(str(bad)), lambda: S.ensure_server(str(tmp_path / "nope"))):
            try:
                f(); assert False
            except S.ScrcpyError:
                pass
        cache = tmp_path / "cache"
        p = S.ensure_server(None, str(cache), fetch=lambda u, d: open(d, "wb").write(b"pinned"))
        assert open(p, "rb").read() == b"pinned"
        assert S.ensure_server(None, str(cache), fetch=lambda u, d: 1 / 0) == p   # cached, no fetch
        try:
            S.ensure_server(None, str(tmp_path / "c2"), fetch=lambda u, d: open(d, "wb").write(b"evil"))
            assert False
        except S.ScrcpyError:
            pass
        assert not os.listdir(tmp_path / "c2")                                   # nothing kept
    finally:
        S.SHA256 = old


def test_scrcpy_session_reconnects():
    sys.path.insert(0, "src")
    from kakao_cli import scrcpy as S

    class Flaky(S.ScrcpySession):
        calls = 0
        def _connect(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise S.ScrcpyError("boom")
            self.connected = True
        def _close(self):
            self.connected = False
        def _cleanup_device(self):
            pass
    s = Flaky(dev=None, server_path="x")
    s.start()
    try:
        for _ in range(60):
            if s.connected:
                break
            time.sleep(0.05)
        assert s.connected and Flaky.calls == 2 and s.error is None
        s.connected = False                          # simulate a dropped socket
        for _ in range(80):
            if Flaky.calls >= 3 and s.connected:
                break
            time.sleep(0.05)
        assert Flaky.calls >= 3 and s.connected
    finally:
        s.stop()


class _FakeBridge:
    def __init__(self):
        self.connected = True; self.on_change = None; self.sets = []; self.seq = 0; self.text = ""
    def clip(self):
        return self.seq, self.text
    def get_clipboard(self):
        return self.seq, self.text
    def set_clipboard(self, text, paste=False, timeout=3.0):
        self.sets.append((text, paste)); return True


def test_clipboard_api(tmp_path):
    import threading
    sys.path.insert(0, "src")
    from kakao_cli import webui
    from kakao_cli.scrcpy import CLIP_MAX
    worker = _StubWorker(); bridge = _FakeBridge()
    httpd = webui.serve(worker, "127.0.0.1", 0, fs_root=str(tmp_path), bridge=bridge)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        snap0 = _json.loads(_http("GET", base + "/api/state")[1])
        bridge.seq, bridge.text = 1, "기기에서 복사"
        bridge.on_change()                            # device copy → state seq bumps
        snap = _json.loads(_http("GET", base + "/api/state")[1])
        assert snap["clip_seq"] == 1 and snap["seq"] > snap0["seq"] and snap["scrcpy"] is True
        assert "기기에서" not in _json.dumps(snap, ensure_ascii=False)   # text not in polls
        st, b = _http("GET", base + "/api/clip")
        assert st == 200 and _json.loads(b)["text"] == "기기에서 복사"
        st, body = _http("POST", base + "/api/clip/read", {})
        assert st == 200 and _json.loads(body)["text"] == "기기에서 복사"
        def read_timeout():
            raise TimeoutError("no reply")
        read_clipboard = bridge.get_clipboard
        bridge.get_clipboard = read_timeout
        assert _http("POST", base + "/api/clip/read", {})[0] == 504
        bridge.get_clipboard = read_clipboard
        assert _req(base, "POST", "/api/clip/read", b'{}', {"Content-Type": "text/plain"})[0] == 415
        assert _req(base, "POST", "/api/clip/read", b'{}', {"Content-Type": "application/json", "Origin": "http://evil.invalid"})[0] == 403
        assert _http("POST", base + "/api/clip", {"text": "붙여넣기", "paste": True})[0] == 200
        assert bridge.sets == [("붙여넣기", True)]
        assert _http("POST", base + "/api/clip", {"text": ""})[0] == 400
        assert _http("POST", base + "/api/clip", {"text": "x" * (CLIP_MAX + 1)})[0] == 400
        assert _req(base, "POST", "/api/clip", b'{"text":"a"}', {"Content-Type": "text/plain"})[0] == 415
        bridge.connected = False                      # no scrcpy: paste → ADBKeyBoard typing
        assert _http("POST", base + "/api/clip/read", {})[0] == 503
        assert _http("POST", base + "/api/clip", {"text": "타이핑", "paste": True})[0] == 200
        assert ("screentext", "타이핑") in worker.puts
        assert _http("POST", base + "/api/clip", {"text": "x"})[0] == 503
    finally:
        httpd.shutdown()

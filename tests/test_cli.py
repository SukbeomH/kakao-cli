import json
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


def test_doctor_no_device_is_structured_error():
    # No adb/device in CI -> must fail with non-zero and JSON error, not a traceback.
    r = run(["doctor", "--json"])
    assert r.returncode != 0
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert payload["error"] in {"adb_unavailable", "device_not_found", "app_not_installed"}


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


def test_webui_serves_page_and_api_and_send():
    sys.path.insert(0, "src")
    from kakao_cli import webui  # noqa: E402

    worker = _StubWorker()
    httpd = webui.serve(worker, "127.0.0.1", 0)
    port = httpd.server_address[1]
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        # GET / -> HTML page with the shared hint text
        status, body = _http("GET", base + "/")
        assert status == 200 and "kakao-cli" in body and "Tab" in body
        # GET /api/state -> JSON snapshot
        status, body = _http("GET", base + "/api/state")
        assert status == 200
        snap = _json.loads(body)
        assert set(("seq", "rooms", "room", "messages", "status")) <= set(snap)
        # send before a room is open -> 400
        status, _ = _http("POST", base + "/api/send", {"text": "안녕"})
        assert status == 400
        # simulate the worker opening a room, then send Korean
        worker.events.put(("opened", "me"))
        _wait_room(base, "me")
        status, _ = _http("POST", base + "/api/send", {"text": "안녕 한글"})
        assert status == 200
        assert ("send", "안녕 한글") in worker.puts
    finally:
        httpd.shutdown()


def _http_bytes(url, data: bytes):
    import urllib.request
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_webui_sendfile_streams_and_sanitizes():
    import os
    import threading
    import urllib.parse
    sys.path.insert(0, "src")
    from kakao_cli import webui  # noqa: E402

    worker = _StubWorker()
    httpd = webui.serve(worker, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        # no room open -> 400
        status, _ = _http_bytes(base + "/api/sendfile?name=a.zip", b"PK\x03\x04data")
        assert status == 400
        # open a room, then upload with a path-traversal name
        worker.events.put(("opened", "me"))
        _wait_room(base, "me")
        payload = b"PK\x03\x04" + b"x" * 2048
        qname = urllib.parse.quote("../../etc/pa ss\"wd.zip")
        status, body = _http_bytes(base + f"/api/sendfile?name={qname}", payload)
        assert status == 200
        # command queued with a temp path; basename is sanitized to the leaf
        cmd = next(c for c in worker.puts if c[0] == "sendfile")
        path = cmd[1]
        leaf = os.path.basename(path)
        assert "/" not in leaf and ".." not in leaf
        assert leaf.endswith("pa_ss_wd.zip")
        with open(path, "rb") as f:
            assert f.read() == payload   # streamed to disk intact
        os.unlink(path)
    finally:
        httpd.shutdown()


def test_webui_safe_name():
    sys.path.insert(0, "src")
    from kakao_cli.webui import _safe_name
    assert _safe_name("../../x.png") == "x.png"
    assert _safe_name("a b.pdf") == "a_b.pdf"
    assert _safe_name("사진.jpg") == "사진.jpg"     # Hangul preserved
    assert _safe_name("") == "file"

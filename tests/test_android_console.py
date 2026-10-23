"""Independent package boundaries, shared behavior and isolated runtime export."""
import http.client
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from android_console import adb, consolelock, scrcpy, video, webui
from android_console.cli import build_parser, main
from android_console.errors import ConsoleBusy, DeviceNotFound
from android_console.runtime import export_runtime
from android_console.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


def test_shared_identity_and_kakao_policy():
    from kakao_cli import adb as kakao_adb, fs as kakao_fs, fmp4 as kakao_fmp4
    from kakao_cli import scrcpy as kakao_scrcpy, webui as kakao_webui
    from kakao_cli.screen import ScreenBuffer
    from android_console import fs, fmp4, screen
    from kakao_cli.worker import Worker as KakaoWorker
    assert kakao_scrcpy is scrcpy and kakao_fs is fs and kakao_fmp4 is fmp4
    assert issubclass(kakao_adb.Device, adb.Device) and issubclass(KakaoWorker, Worker)
    assert ScreenBuffer is screen.ScreenBuffer
    assert kakao_webui.PointerInput is webui.PointerInput
    assert 'id="ckhome"' in webui.PAGE and 'id="ckrecent"' in webui.PAGE
    assert 'value="mirror" selected' in webui.PAGE
    assert 'id="ckhome"' not in kakao_webui.PAGE
    assert 'id="ckrecent"' not in kakao_webui.PAGE
    assert 'value="virtual" selected' in kakao_webui.PAGE
    assert 'kakao.video' in kakao_webui.PAGE and 'android-console.video' in webui.PAGE
    assert 'X-Kakao-Op' in kakao_webui.PAGE and 'X-Android-Console-Op' in webui.PAGE
    assert kakao_webui.PROFILE.target_app == "com.kakao.talk"


def test_no_kakao_imports_in_standalone_process():
    source = '''import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, *args):
  if fullname.startswith("kakao_cli") or fullname.startswith("cryptography"):
   raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from android_console import adb, webui, worker, runtime
from android_console.cli import main
assert main(["--version"]) is None
'''.replace('assert main(["--version"]) is None', 'try: main(["--version"])\nexcept SystemExit as e: assert e.code == 0')
    subprocess.run([sys.executable, "-c", source], cwd="/tmp", check=True, capture_output=True)


def test_worker_direct_device_order_failure_and_shutdown():
    dev = Mock()
    worker = Worker(dev, poll_interval=0.01)
    worker.start()
    try:
        worker.put_wait("screentext", "한글")
        worker.put_wait("key", "enter")
        worker.put_wait("key", "recent")
        assert dev.method_calls[:3] == [
            ("type_text", ("한글",), {}), ("keyevent", ("KEYCODE_ENTER",), {}),
            ("keyevent", ("KEYCODE_APP_SWITCH",), {}),
        ]
        dev.type_text.side_effect = RuntimeError("input failure")
        with pytest.raises(RuntimeError, match="input failure"):
            worker.put_wait("screentext", "bad")
        worker.put_wait("key", "back")
    finally:
        worker.put("quit")
        worker.join(timeout=1)
    assert not worker.is_alive()


def test_console_server_uses_direct_worker_and_profile(tmp_path):
    dev = Mock()
    worker = Worker(dev)
    worker.start()
    server = webui.serve(worker, "127.0.0.1", 0, tmpdir=str(tmp_path), fs_root=str(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
    try:
        connection.request("GET", "/")
        page = connection.getresponse().read().decode()
        assert 'id="ckrecent"' in page and "@@" not in page
        connection.request("POST", "/api/screen/key", body=json.dumps({"name": "recent"}),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200, body
        dev.keyevent.assert_called_with("KEYCODE_APP_SWITCH")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.put("quit")
        worker.join(timeout=1)


def bridge():
    result = Mock()
    result.on_connect = None
    result.connected = True
    result.video = True
    return result


def test_generic_mirror_does_not_launch_any_app_or_claim_lease():
    br, dev, lease = bridge(), Mock(), Mock()
    hub = video.VideoHub(br, dev, lease=lease)
    try:
        hub.subscribe("h264", (800, 600, 1))
        br.on_connect()
        lease.refresh.assert_not_called()
        br.send.assert_not_called()
        assert "new_display" not in br.set_video.call_args.kwargs
        hub.set_mode("virtual")
        assert br.set_video.call_args.kwargs["vd_system_decorations"] == "true"
        br.on_connect()
        br.send.assert_not_called()
    finally:
        hub.close()


def test_virtual_target_optional_and_failed_claim_rolls_back():
    br, lease = bridge(), Mock()
    hub = video.VideoHub(br, Mock(), lease=lease, target_app="com.android.settings")
    try:
        hub.subscribe("h264", (800, 600, 1))
        lease.refresh.side_effect = ConsoleBusy("busy")
        with pytest.raises(ConsoleBusy):
            hub.set_mode("virtual")
        assert hub.mode == "mirror" and hub.active
        lease.refresh.side_effect = None
        hub.set_mode("virtual")
        br.on_connect()
        br.send.assert_called_with(scrcpy.enc_start_app("com.android.settings"))
    finally:
        hub.close()


def test_owner_scoped_lock_across_independent_sessions(tmp_path, monkeypatch):
    lock = tmp_path / "console.lock"
    monkeypatch.setattr(consolelock, "LOCK", str(lock))
    class Shell:
        def shell(self, command):
            return subprocess.check_output(["sh", "-c", command], text=True)
    one, two = consolelock.Lease(Shell()), consolelock.Lease(Shell())
    one.refresh()
    fresh, info = consolelock.parse(lock.read_text() + "==\n" + str(int(__import__('time').time())))
    assert fresh and info["mode"] == "virtual"
    two.clear()
    assert lock.exists()
    with pytest.raises(ConsoleBusy):
        two.refresh()
    one.refresh()
    one.clear()
    assert not lock.exists()
    two.refresh()
    one.clear()
    assert lock.exists()
    two.clear()


def test_session_cleanup_never_removes_another_session_jar():
    dev = Mock()
    first, second = scrcpy.ScrcpySession(dev, "server"), scrcpy.ScrcpySession(dev, "server")
    assert first.device_jar != second.device_jar
    first._cleanup_device()
    assert second.device_jar not in dev.shell.call_args.args[0]


def test_multiple_devices_require_selection_and_connect_selects_address(monkeypatch):
    monkeypatch.setattr(adb, "list_devices", lambda _: ["phone", "vm:5555"])
    monkeypatch.setattr(adb.subprocess, "run", Mock())
    with pytest.raises(DeviceNotFound, match="multiple"):
        adb.connect()
    assert adb.connect(addr="vm:5555").serial == "vm:5555"


def test_unicode_input_is_shell_quoted():
    device = adb.Device("fixture")
    device.shell = Mock()
    text = '한글 " \' $HOME $(touch /tmp/never) `id`\nnext'
    device.type_text(text)
    assert shlex.split(device.shell.call_args.args[0]) == ["am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text]


def test_runtime_export_is_self_contained_and_does_not_overwrite(tmp_path):
    destination = export_runtime(tmp_path / "runtime")
    assert (destination / "python-package/src/android_console/webui.py").is_file()
    assert not list(destination.rglob("*.pyc"))
    assert not list(destination.rglob("*.apk"))
    assert not list(destination.rglob("kakao_cli"))
    assert "cpus: 2" in (destination / "lima.yaml").read_text()
    assert "memory: 4GiB" in (destination / "lima.yaml").read_text()
    assert "disk: 20GiB" in (destination / "lima.yaml").read_text()
    assert "static: true" in (destination / "lima.yaml").read_text()
    compose = (destination / "compose.yaml").read_text()
    assert "/opt/android-console/data" in compose and "8801:8801" in compose
    for script in destination.glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)
    if shutil.which("limactl"):
        subprocess.run(["limactl", "validate", str(destination / "lima.yaml")], check=True, capture_output=True)
    sentinel = destination / "README.md"
    before = sentinel.read_bytes()
    with pytest.raises(FileExistsError):
        export_runtime(destination)
    assert sentinel.read_bytes() == before


def test_cli_defaults_and_validation():
    args = build_parser().parse_args(["serve"])
    assert args.port == 8801 and args.video_mode == "mirror" and args.app is None
    with pytest.raises(SystemExit) as port_error:
        main(["serve", "--port", "65536"])
    assert port_error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(["serve", "--app", "$(id)"])
    assert error.value.code == 2


@pytest.mark.parametrize("mode", ["fresh", "existing", "bad_hash", "bad_signer", "adb_failure", "ime_timeout"])
def test_runtime_keyboard_first_install_and_verification(tmp_path, mode):
    script = tmp_path / "install-keyboard.py"
    shutil.copy(ROOT / "packages/android-console/src/android_console/runtime_assets/install-keyboard.py", script)
    apk = b"keyboard fixture"
    pin = {"url": "https://example.test/keyboard.apk", "sha256": hashlib.sha256(apk).hexdigest(),
           "certificate_sha256": "fixture-signer"}
    if mode == "bad_hash":
        pin["sha256"] = "0" * 64
    (tmp_path / "keyboard.json").write_text(json.dumps(pin))
    spec = importlib.util.spec_from_file_location("keyboard_installer", script)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    calls = []
    ime_checks = []

    def output(command, **kwargs):
        calls.append(command)
        if command[0] == "apksigner":
            return "Signer #1 certificate SHA-256 digest: " + (
                "wrong" if mode == "bad_signer" else pin["certificate_sha256"])
        assert command[:3] == ["adb", "-s", "127.0.0.1:5555"]
        if command[-1] == "pm list packages com.android.adbkeyboard":
            if mode == "adb_failure":
                raise subprocess.CalledProcessError(1, command)
            return "package:com.android.adbkeyboard\n" if mode == "existing" else ""
        if command[-1] == "ime list -a -s":
            ime_checks.append(command)
            return "" if mode == "ime_timeout" or (mode == "fresh" and len(ime_checks) < 3) else "com.android.adbkeyboard/.AdbIME\n"
        if command[-1] == "settings get secure default_input_method":
            return "com.android.adbkeyboard/.AdbIME\n"
        return "Success"

    download = Mock(side_effect=lambda *a, **k: io.BytesIO(apk))
    installer.subprocess = SimpleNamespace(check_output=output, run=lambda command, **kw: calls.append(command))
    installer.urllib = SimpleNamespace(request=SimpleNamespace(urlopen=download))
    installer.time = SimpleNamespace(sleep=Mock())
    if mode in ("bad_hash", "bad_signer", "adb_failure", "ime_timeout"):
        with pytest.raises((ValueError, RuntimeError, subprocess.CalledProcessError)):
            installer.main()
        assert not any(command[-1].startswith("ime set ") for command in calls)
        if mode != "ime_timeout":
            assert not any(command[0] == "adb" and command[3] == "install" for command in calls)
    else:
        installer.main()
        installs = [command for command in calls if command[0] == "adb" and command[3] == "install"]
        assert len(installs) == (mode == "fresh")
        assert calls[-1][-1] == "settings get secure default_input_method"
        if mode == "existing":
            download.assert_not_called()

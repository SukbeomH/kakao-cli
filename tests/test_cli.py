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


def test_doctor_no_device_is_structured_error():
    # No adb server/device in CI -> must fail with non-zero and JSON error, not a traceback.
    r = run(["doctor", "--json"])
    assert r.returncode != 0
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert payload["error"] in {"adb_unavailable", "device_not_found", "app_not_installed"}


def test_notimpl_command():
    r = run(["send", "--json"])
    assert r.returncode == 1
    assert json.loads(r.stdout)["ok"] is False

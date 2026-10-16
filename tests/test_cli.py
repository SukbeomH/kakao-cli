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

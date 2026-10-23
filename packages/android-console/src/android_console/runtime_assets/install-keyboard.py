#!/usr/bin/env python3
"""Explicit, hash- and signer-pinned optional Unicode IME installation."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request


def main():
    pin = json.loads(Path(__file__).with_name("keyboard.json").read_text())
    def adb(*args):
        return subprocess.check_output(["adb", "-s", "127.0.0.1:5555", *args], text=True)
    installed = adb("shell", "pm list packages com.android.adbkeyboard").splitlines()
    if "package:com.android.adbkeyboard" not in installed:
        subprocess.run(["apt-get", "update"], check=True)
        subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "apksigner", "default-jre-headless"], check=True)
        with tempfile.TemporaryDirectory(prefix="android-console-ime-") as directory:
            apk = Path(directory) / "ADBKeyboard.apk"
            with urllib.request.urlopen(pin["url"], timeout=60) as response, apk.open("wb") as stream:
                while chunk := response.read(65536):
                    stream.write(chunk)
            if hashlib.sha256(apk.read_bytes()).hexdigest() != pin["sha256"]:
                raise ValueError("ADBKeyBoard SHA-256 mismatch")
            certs = subprocess.check_output(["apksigner", "verify", "--print-certs", str(apk)], text=True)
            if "Signer #1 certificate SHA-256 digest: " + pin["certificate_sha256"] not in certs.splitlines():
                raise ValueError("ADBKeyBoard signer mismatch")
            adb("install", str(apk))
    # Package installation completes before InputMethodManager has necessarily
    # processed the package-added event on a fresh Android boot.
    component = "com.android.adbkeyboard/.AdbIME"
    for _ in range(60):
        if component in adb("shell", "ime list -a -s").splitlines():
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("ADBKeyBoard IME registration timed out")
    adb("shell", "ime enable " + component)
    adb("shell", "ime set " + component)
    if adb("shell", "settings get secure default_input_method").strip() != component:
        raise RuntimeError("ADBKeyBoard was not selected")


if __name__ == "__main__":
    main()

"""Ordered native IME composition; the selected device is always explicit."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import time

PACKAGE = "org.androidconsole.ime"
COMPONENT = PACKAGE + "/.ConsoleIME"
ACTION = PACKAGE + ".COMPOSITION"


def validate(body):
    session, phase, text = body.get("session"), body.get("phase"), body.get("text", "")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}:[0-9]{1,16}", session):
        raise ValueError("bad composition session")
    if phase not in ("start", "update", "commit"):
        raise ValueError("bad composition phase")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 32768 or (phase == "start" and text):
        raise ValueError("bad composition text")
    return {"session": session, "phase": phase, "text": text}


class CompositionInput:
    def __init__(self, dev):
        self.dev, self.ready = dev, False

    def _prepare(self):
        selected = self.dev.shell("settings get secure default_input_method").strip()
        allowed = {COMPONENT, PACKAGE + "/" + PACKAGE + ".ConsoleIME",
                   "com.android.adbkeyboard/.AdbIME", "com.android.adbkeyboard/com.android.adbkeyboard.AdbIME"}
        if selected not in allowed:
            raise RuntimeError("한글 실시간 조합에는 ADBKeyBoard 또는 Android Console Input 선택이 필요합니다")
        assets = Path(__file__).with_name("ime_assets")
        apk = assets / "android-console-ime.apk"
        digest = (assets / "android-console-ime.sha256").read_text().strip()
        if hashlib.sha256(apk.read_bytes()).hexdigest() != digest:
            raise RuntimeError("console IME package hash mismatch")
        # Install only on the first composition in this worker; never during a GET.
        installed = self.dev.shell("pm path " + PACKAGE).strip()
        path = installed.removeprefix("package:").splitlines()[0] if installed.startswith("package:") else ""
        current_hash = self.dev.shell("sha256sum " + shlex.quote(path)).split() if path else []
        if not current_hash or current_hash[0] != digest:
            # Updating the selected IME otherwise kills its service without rebinding.
            if selected.startswith(PACKAGE + "/"):
                self.dev.shell("ime disable " + COMPONENT)
            self.dev._run(["install", "-r", str(apk)], timeout=10, check=True)
        own = {COMPONENT, PACKAGE + "/" + PACKAGE + ".ConsoleIME"}
        # Package installation finishes before InputMethodManager sees the new
        # service. Wait for registration, then confirm selection before input.
        for _ in range(30):
            registered = set(self.dev.shell("ime list -a -s").split())
            if registered & own:
                self.dev.shell("ime enable " + COMPONENT)
                self.dev.shell("ime set " + COMPONENT)
                if self.dev.shell("settings get secure default_input_method").strip() in own:
                    self.ready = True
                    return
            time.sleep(.1)
        raise RuntimeError("console IME registration/selection timed out")

    def send(self, body):
        body = validate(body)
        preparing = not self.ready
        if preparing:
            if body["phase"] != "start":
                raise RuntimeError("composition must start first")
            self._prepare()
        payload = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
        command = "am broadcast -p " + PACKAGE + " -a " + ACTION + " --es payload " + shlex.quote(payload)
        # Switching IME may briefly leave no InputConnection. Retry only an empty
        # start: text updates and commits must never be replayed after uncertainty.
        attempts = 10 if preparing else 1
        for attempt in range(attempts):
            result = self.dev.shell(command, timeout=3)
            if re.search(r"\bresult=200\b", result) and "android-console-ime/1" in result:
                return
            if not preparing or not re.search(r"\bresult=(?:0|409)\b", result):
                break
            time.sleep(.1)
        raise RuntimeError("한글 조합을 적용하지 못했습니다. 기기 입력 칸을 다시 선택하세요")

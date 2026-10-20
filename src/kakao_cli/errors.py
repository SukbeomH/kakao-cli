"""Stable, machine-parseable error codes. Every failure -> non-zero exit + JSON."""
from __future__ import annotations


class KakaoError(Exception):
    code = "error"
    exit_code = 1

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.context}


class AdbUnavailable(KakaoError):
    code = "adb_unavailable"
    exit_code = 3


class DeviceNotFound(KakaoError):
    code = "device_not_found"
    exit_code = 4


class AppNotInstalled(KakaoError):
    code = "app_not_installed"
    exit_code = 5


class SendUncertain(KakaoError):
    """Transcript readback could not confirm the message was delivered. Never auto-retry."""
    code = "send_uncertain"
    exit_code = 6


class ConsoleBusy(KakaoError):
    """The web console has KakaoTalk on a scrcpy virtual display; UI automation
    (uiautomator/input on display 0) would act on the wrong screen."""
    code = "console_busy"
    exit_code = 7

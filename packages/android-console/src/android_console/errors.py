"""Stable, machine-parseable error codes. Every failure -> non-zero exit + JSON."""
from __future__ import annotations


class ConsoleError(Exception):
    code = "error"
    exit_code = 1

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.context}


class AdbUnavailable(ConsoleError):
    code = "adb_unavailable"
    exit_code = 3


class DeviceNotFound(ConsoleError):
    code = "device_not_found"
    exit_code = 4


class AppNotInstalled(ConsoleError):
    code = "app_not_installed"
    exit_code = 5


class ConsoleBusy(ConsoleError):
    """The web console has an app on a scrcpy virtual display; UI automation
    (uiautomator/input on display 0) would act on the wrong screen."""
    code = "console_busy"
    exit_code = 7

"""adb transport layer. Thin wrapper over adbutils; isolates the one dependency."""
from __future__ import annotations
from dataclasses import dataclass

from .errors import AdbUnavailable, DeviceNotFound, AppNotInstalled
from . import PACKAGE


@dataclass
class Device:
    serial: str
    _d: object  # adbutils device

    def shell(self, cmd: str, timeout: float = 20.0) -> str:
        return self._d.shell(cmd, timeout=timeout)

    def app_version(self, package: str = PACKAGE) -> str | None:
        out = self.shell(f"dumpsys package {package} | grep versionName")
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("versionName="):
                return line.split("=", 1)[1]
        return None

    def current_activity(self) -> str | None:
        out = self.shell("dumpsys activity activities | grep -m1 ResumedActivity")
        return out.strip() or None

    def ui_dump(self) -> str:
        """Raw UIAutomator XML of the current screen."""
        return self._d.dump_hierarchy()


def connect(serial: str | None = None, addr: str | None = None) -> Device:
    try:
        import adbutils
    except ImportError as e:  # pragma: no cover
        raise AdbUnavailable("adbutils not installed; run `uv sync`") from e

    client = adbutils.AdbClient()
    try:
        if addr:
            client.connect(addr)
        devices = client.device_list()
    except Exception as e:
        raise AdbUnavailable(f"cannot reach adb server: {e}") from e

    if not devices:
        raise DeviceNotFound("no adb devices; check `adb connect <vm-ip>:5555`")
    if serial:
        for d in devices:
            if d.serial == serial:
                return Device(d.serial, d)
        raise DeviceNotFound(f"serial not found: {serial}", available=[d.serial for d in devices])
    return Device(devices[0].serial, devices[0])


def require_app(dev: Device, package: str = PACKAGE) -> str:
    v = dev.app_version(package)
    if v is None:
        raise AppNotInstalled(f"{package} not installed on {dev.serial}")
    return v

"""KakaoTalk defaults over the shared Android ADB transport."""
from android_console.adb import Device as AndroidDevice, list_devices
from android_console.adb import connect as _connect, require_app as _require_app
from . import PACKAGE


class Device(AndroidDevice):
    def app_version(self, package=PACKAGE):
        return super().app_version(package)

    def current_activity(self, package=PACKAGE):
        return super().current_activity(package)


def connect(serial=None, addr=None, adb="adb"):
    return _connect(serial, addr, adb, device_type=Device)


def require_app(dev, package=PACKAGE):
    return _require_app(dev, package)

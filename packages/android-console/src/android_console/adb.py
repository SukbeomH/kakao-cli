"""adb transport via subprocess. No third-party deps — the control host just needs
the `adb` binary and a reachable device. Unicode text goes through ADBKeyBoard IME.
"""
from __future__ import annotations
from dataclasses import dataclass
import subprocess
import shlex
import time

from .errors import AdbUnavailable, DeviceNotFound, AppNotInstalled


@dataclass
class Device:
    serial: str
    adb: str = "adb"

    def _run(self, args: list[str], timeout: float = 30.0, check: bool = False) -> str:
        p = subprocess.run([self.adb, "-s", self.serial, *args],
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        if check and p.returncode != 0:
            raise AdbUnavailable(f"adb {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout

    def shell(self, cmd: str, timeout: float = 30.0) -> str:
        return self._run(["shell", cmd], timeout=timeout)

    def app_version(self, package: str) -> str | None:
        for line in self.shell(f"dumpsys package {shlex.quote(package)} | grep versionName").splitlines():
            line = line.strip()
            if line.startswith("versionName="):
                return line.split("=", 1)[1]
        return None

    def current_activity(self, package: str | None = None) -> str | None:
        out = self.shell("dumpsys activity activities | grep -m1 topResumedActivity")
        for tok in out.split():
            if "/" in tok and (package is None or tok.startswith(package + "/")):
                return tok.rstrip("}")
        return None

    def dump(self) -> str:
        """UI XML을 메모리로만 수신한다. 본문/이름이 있는 dump 파일을 만들지 않는다."""
        out = self._run(["exec-out", "uiautomator dump /dev/tty"])
        start, end = out.find("<?xml"), out.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise AdbUnavailable("메모리 UI dump를 얻지 못했습니다")
        return out[start:end + len("</hierarchy>")]

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {x} {y}")

    def back(self) -> None:
        self.shell("input keyevent 4")

    def type_text(self, text: str) -> None:
        """Unicode-safe text entry via ADBKeyBoard (must be installed + selected)."""
        self.shell('am broadcast -a ADB_INPUT_TEXT --es msg ' + shlex.quote(text))

    def clear_text(self) -> None:
        self.shell("am broadcast -a ADB_CLEAR_TEXT")


    def push(self, local: str, remote: str) -> None:
        import subprocess as _sp
        p = _sp.run([self.adb, "-s", self.serial, "push", local, remote],
                    capture_output=True, text=True, timeout=120, stdin=_sp.DEVNULL)
        if p.returncode != 0:
            raise AdbUnavailable(f"adb push failed: {p.stderr.strip()}")

    def pull(self, remote: str, local: str) -> None:
        import subprocess as _sp
        p = _sp.run([self.adb, "-s", self.serial, "pull", remote, local],
                    capture_output=True, text=True, timeout=120, stdin=_sp.DEVNULL)
        if p.returncode != 0:
            raise AdbUnavailable(f"adb pull failed: {p.stderr.strip()}")

    def media_content_uri(self, device_path: str, kind: str) -> str | None:
        """Scan a pushed file into MediaStore and return its content:// URI."""
        import re as _re, time as _t
        base = device_path.rsplit("/", 1)[-1]
        if kind == "image":
            cols = [("images", "images")]
        elif kind == "video":
            cols = [("video", "video")]
        else:  # documents: generic file + downloads collections
            cols = [("file", "file"), ("downloads", "downloads")]
        self.shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                   f"-d file://{device_path}")
        for _ in range(25):
            for col, path in cols:
                suffix = "/media" if col in ("images", "video", "file") else ""
                uri = f"content://media/external/{col}{suffix}"
                out = self.shell(f"content query --uri {uri} --projection _id:_data")
                for line in out.splitlines():
                    if base in line:
                        m = _re.search(r"_id=(\d+)", line)
                        if m:
                            return f"{uri}/{m.group(1)}"
            _t.sleep(1.0)
        return None

    def send_intent(self, uri: str, mime: str, component: str) -> None:
        self.shell(f"am start -a android.intent.action.SEND -t {mime} "
                   f"--eu android.intent.extra.STREAM {uri} "
                   f"--grant-read-uri-permission -n {component}")

    def screencap(self, local_path: str) -> None:
        with open(local_path, "wb") as f:
            p = subprocess.run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"],
                               stdout=f, timeout=30, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise AdbUnavailable("screencap failed")

    def screencap_bytes(self) -> bytes:
        """Grab the current screen as a PNG, in memory (never written to disk)."""
        p = subprocess.run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise AdbUnavailable("screencap failed")
        return p.stdout

    def open_exec_out(self, cmd: str) -> subprocess.Popen:
        """Stream a device command's raw stdout (binary-clean, e.g. `cat FILE`).
        Caller reads .stdout and must kill()/wait() the process."""
        return subprocess.Popen([self.adb, "-s", self.serial, "exec-out", cmd],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 200) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def keyevent(self, code) -> None:
        self.shell(f"input keyevent {code}")


def list_devices(adb: str = "adb") -> list[str]:
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL).stdout
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        raise AdbUnavailable(f"adb not usable: {e}") from e
    devs = []
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            devs.append(line.split("\t")[0])
    return devs


def connect(serial: str | None = None, addr: str | None = None, adb: str = "adb", device_type=Device) -> Device:
    if addr:
        subprocess.run([adb, "connect", addr], capture_output=True, text=True, timeout=20,
                       stdin=subprocess.DEVNULL)
    serial = serial or addr
    devs = list_devices(adb)
    if not devs:
        raise DeviceNotFound("no adb devices; check `adb connect <host>:5555`")
    if serial:
        if serial not in devs:
            raise DeviceNotFound(f"serial not found: {serial}", available=devs)
        return device_type(serial, adb)
    if len(devs) != 1:
        raise DeviceNotFound("multiple adb devices; specify --serial", available=devs)
    return device_type(devs[0], adb)


def require_app(dev: Device, package: str) -> str:
    v = dev.app_version(package)
    if v is None:
        raise AppNotInstalled(f"{package} not installed on {dev.serial}")
    return v

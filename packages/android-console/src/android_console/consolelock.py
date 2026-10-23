"""Shared device-side lock (legacy path retained for older CLI compatibility): "the web console is driving Android on a virtual display".

While the console's scrcpy virtual display is up, Android lives on that
display: `uiautomator dump` follows the focused app there but `input tap`
still targets display 0, so CLI/TUI automation would silently tap the wrong
screen. The console writes a heartbeat file on the *device* (shared truth for
CLIs on any host) and the CLI refuses with a clear error while it is fresh.
Times come from the device clock, so host clock skew doesn't matter.
"""
from __future__ import annotations
import os
import socket

from .errors import ConsoleBusy

LOCK = "/data/local/tmp/kakao-cli-console.lock"
STALE_S = 20          # heartbeat every 5 s; older than this = the console died


def write(dev, mode: str):
    dev.shell("echo $(date +%%s) %s %d %s > %s" % (socket.gethostname(), os.getpid(), mode, LOCK))


def clear(dev):
    dev.shell("rm -f %s" % LOCK)


def parse(out: str):
    """`cat LOCK; echo ==; date +%s` → (fresh: bool, info: dict|None)."""
    body, _, now = (out or "").partition("==")
    parts = body.split()
    try:
        ts, now_i = int(parts[0]), int(now.strip())
    except (ValueError, IndexError):
        return False, None
    info = {"host": parts[1] if len(parts) > 1 else "?", "pid": parts[2] if len(parts) > 2 else "?",
            "mode": parts[3] if len(parts) > 3 else "?", "age_s": now_i - ts}
    return 0 <= now_i - ts <= STALE_S, info


def check(dev):
    """Raise ConsoleBusy if a live web console holds the device."""
    fresh, info = parse(dev.shell("cat %s 2>/dev/null; echo ==; date +%%s" % LOCK))
    if fresh:
        raise ConsoleBusy(
            "웹 콘솔이 가상 디스플레이로 앱을 사용 중입니다 — 콘솔 화면을 닫거나(최대 %ds 후 해제) "
            "콘솔을 미러 모드로 바꾼 뒤 다시 실행하세요" % STALE_S, **(info or {}))


class Lease:
    """Owner-scoped virtual-display lease, compatible with older CLI readers.

    Serialize claim/refresh/release on-device; a foreign fresh lease is never
    overwritten and a mirror console never releases another console's lease.
    """
    def __init__(self, dev):
        import uuid
        self.dev = dev
        self.owner = uuid.uuid4().hex

    def _command(self, release=False):
        # Values are constants, an integer, or generated hex; no caller shell input.
        guard = LOCK + ".guard"
        action = ('if [ "$owner" = "%s" ]; then rm -f %s; fi' % (self.owner, LOCK)
                  if release else
                  'if [ "$owner" != "%s" ] && [ "$age" -ge 0 ] && [ "$age" -le %d ]; '
                  'then echo BUSY; else echo "$now %s %d virtual" > %s; fi'
                  % (self.owner, STALE_S, self.owner, os.getpid(), LOCK))
        return ('mkdir %s 2>/dev/null || { echo BUSY; exit 0; }; '
                "trap 'rmdir %s' EXIT; now=$(date +%%s); ts=0; owner=; "
                'read ts owner pid mode < %s 2>/dev/null || true; '
                'case "$ts" in ""|*[!0-9]*) ts=0;; esac; age=$((now-ts)); %s'
                % (guard, guard, LOCK, action))

    def refresh(self):
        if "BUSY" in self.dev.shell(self._command()).split():
            raise ConsoleBusy("다른 콘솔이 이 기기의 가상 화면을 사용 중입니다")

    def clear(self):
        self.dev.shell(self._command(release=True))

"""DB 우선 수신과 잠금 확인 후 UI 폴백. 경고에는 개인정보를 넣지 않는다."""
from __future__ import annotations
import sys
from . import consolelock
from .driver import Driver
from . import RID
from .errors import KakaoError
from .kakaodb import Database, DBUnavailable


class Receiver:
    def __init__(self, dev, source='db', warning=None):
        self.dev = dev
        self.source = source
        self.db = None
        self.warn = warning or (lambda text: print(text, file=sys.stderr))
        if source == 'db':
            try:
                self.db = Database(dev)
            except DBUnavailable as e:
                self._fallback(e)
        else:
            consolelock.check(dev)

    def _fallback(self, error):
        self.warn('경고: ' + error.message + ' (DB → UI 폴백)')
        consolelock.check(self.dev)  # 잠금 중에는 UI 폴백도 거절한다.
        self.source = 'ui'
        self.db = None

    def list_rooms(self):
        if self.db:
            try:
                self.db.guard()
                return self.db.list_rooms()
            except DBUnavailable as e:
                self._fallback(e)
        consolelock.check(self.dev)
        return Driver(self.dev).list_rooms()

    def read_detailed(self, room, limit=20):
        if self.db:
            try:
                self.db.guard()
                return self.db.read_detailed(room, limit)
            except DBUnavailable as e:
                self._fallback(e)
        consolelock.check(self.dev)
        d = Driver(self.dev)
        d._open_target(room)
        screen = d._screen_where(lambda s: s.first(rid=RID['chat_log']) is not None, timeout=10)
        if screen is None:
            raise KakaoError('chat log not found')
        return d._messages_detailed(screen)[-limit:]

    def poll_room(self, room, interval=3., limit=100):
        if self.db:
            try:
                yield from self.db.poll_room(room, interval, limit)
                return
            except DBUnavailable as e:
                self._fallback(e)
        consolelock.check(self.dev)
        # 기존 UI 폴러는 각 조회에도 잠금을 확인한다.
        yield from Driver(self.dev).poll_room(room, interval, limit)

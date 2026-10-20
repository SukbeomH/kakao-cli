"""가짜 계정/본문만 사용하는 DB 계약·암호·잠금 테스트."""
import base64
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from kakao_cli import cli, kakaodb as db, receiver
from kakao_cli.errors import ConsoleBusy


def encrypt(user, enc, text):
    pad = PKCS7(128).padder()
    plain = pad.update(text.encode()) + pad.finalize()
    ctx = Cipher(algorithms.AES(db.derive_key(db.salt(user, enc))), modes.CBC(db.IV)).encryptor()
    return base64.b64encode(ctx.update(plain) + ctx.finalize()).decode()


@pytest.mark.parametrize('enc', range(32))
def test_fake_roundtrip(enc):
    assert db.decrypt(12345678, enc, encrypt(12345678, enc, '가짜 본문 🐈\n두 줄')) == '가짜 본문 🐈\n두 줄'


@pytest.mark.parametrize('enc', [-1, 32, True, '31', None])
def test_bad_enc(enc):
    with pytest.raises(db.DBUnavailable):
        db.salt(123, enc)


def test_salt_and_key_cache():
    assert db.salt(123, 31) == b'veil123' + bytes(9)
    assert db.salt(123, 30) == b'extr.ursra123' + bytes(3)
    assert db.salt(1234567890123456789, 15) == b'isabel1234567890'
    assert db.salt(0, 31) == bytes(16)
    assert db.decrypt(12, 31, '') == ''
    a = db.derive_key(db.salt(123, 31))
    # WTFPL upstream KDF로 독립 생성한 가짜 user_id=123 벡터.
    assert a.hex() == 'b2d33934a5b537c16d069bc2260856c1a41cb907070537db8fd25d5afc937d2d'
    assert a is db.derive_key(db.salt(123, 31))
    with pytest.raises(db.DBUnavailable) as exc:
        db.decrypt(999, 31, encrypt(123, 31, '가짜 비밀'))
    assert '가짜 비밀' not in str(exc.value)


@pytest.mark.parametrize('bad', ['1;DELETE FROM chat_logs', "1'", -(2**63)-1, True, 1.0, 2**63])
def test_numeric_sql_only(bad):
    with pytest.raises(db.DBUnavailable):
        db.number(bad)


def test_exec_out_readonly_quoting(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout='[{"n":2}]')
    monkeypatch.setattr(db.subprocess, 'run', run)
    obj = object.__new__(db.Database)
    obj.dev = SimpleNamespace(adb='adb', serial='fake')
    assert obj._query('KakaoTalk.db', "SELECT json_extract(v,'$.enc') FROM chat_logs") == [{'n': 2}]
    import shlex
    cmd = shlex.split(seen[0][-1])
    assert cmd[:3] == ['sqlite3', '-readonly', '-json']
    assert cmd[-1] == "SELECT json_extract(v,'$.enc') FROM chat_logs"
    assert seen[0][-2] == 'exec-out'
    with pytest.raises(db.DBUnavailable):
        obj._query('/tmp/other', 'SELECT 1')


def test_read_watermarks():
    assert db.unread_count(100, 2, [1, 2, 3, 4], [0, 0, 100, 99], 1) == 1
    assert db.unread_count(100, 1, [2, 3], [99, 100], 1) == 1
    assert db.unread_count(100, 1, [], [], 1) == 0
    assert db.unread_count(100, 2, [3], [], 1) is None
    assert db.unread_count(100, 2, [3], [0], None) is None
    assert db.number(-1) == '-1'
    assert db.unread_count(100, 2, [3], ['99'], 1) is None


class FakeDB:
    def __init__(self, dev): pass
    def guard(self): pass
    def read_detailed(self, room, limit): return [{'text': '가짜', 'is_mine': True, 'created_at': 123}]
    def list_rooms(self): return []
    def poll_room(self, *a):
        yield {'text': '가짜', '_id': 2}


def busy(dev):
    raise ConsoleBusy('가짜 잠금')


def test_db_allowed_while_console_busy_and_cli_compatible(monkeypatch):
    monkeypatch.setattr(receiver, 'Database', FakeDB)
    monkeypatch.setattr(receiver.consolelock, 'check', busy)
    monkeypatch.setattr(cli, '_dev', lambda args: object())
    args = SimpleNamespace(source='db', room='me', limit=20)
    out = cli.cmd_read(args)
    assert out['messages'] == ['가짜'] and out['message_details'][0]['created_at'] == 123
    assert out['source'] == 'db'
    assert list(receiver.Receiver(object()).poll_room('me'))[0]['_id'] == 2
    args.source = 'ui'
    with pytest.raises(ConsoleBusy): cli.cmd_read(args)


def test_fallback_rechecks_lock_and_redacts(monkeypatch):
    class Broken:
        def __init__(self, dev): raise db.DBUnavailable('지원하지 않는 형식')
    monkeypatch.setattr(receiver, 'Database', Broken)
    monkeypatch.setattr(receiver.consolelock, 'check', busy)
    warnings = []
    with pytest.raises(ConsoleBusy): receiver.Receiver(object(), warning=warnings.append)
    assert len(warnings) == 1 and '폴백' in warnings[0]
    monkeypatch.setattr(receiver.consolelock, 'check', lambda dev: None)
    assert receiver.Receiver(object(), warning=warnings.append).source == 'ui'


def test_mid_poll_fallback_obeys_lock(monkeypatch):
    class Broken(FakeDB):
        def poll_room(self, *args):
            yield {'_id': 1}
            raise db.DBUnavailable('실행 중 DB 형식 변경')
    monkeypatch.setattr(receiver, 'Database', Broken)
    monkeypatch.setattr(receiver.consolelock, 'check', busy)
    it = receiver.Receiver(object(), warning=lambda text: None).poll_room('me')
    assert next(it)['_id'] == 1
    with pytest.raises(ConsoleBusy): next(it)


def test_schema_version_guards():
    obj = object.__new__(db.Database)
    obj.dev = SimpleNamespace(shell=lambda cmd: 'versionCode=1')
    with pytest.raises(db.DBUnavailable): obj.guard()
    obj.dev = SimpleNamespace(shell=lambda cmd: 'versionCode=29260830')
    obj._query = lambda *args: []
    with pytest.raises(db.DBUnavailable): obj.guard()


def test_same_body_distinct_row_and_batch_backlog():
    obj = object.__new__(db.Database)
    obj.guard = lambda: None
    obj.resolve = lambda target: {'id': 1}
    obj.max_id = lambda room: 5
    rows = [{'_id': i, 'text': '같은 가짜 본문'} for i in range(1, 6)]
    obj._query = lambda database, sql: []
    obj.changes = lambda target, after, limit: [r for r in rows if r['_id'] > after][:limit]
    # 초기 MAX=3 → 그 뒤 4/5를 각각 emit. UI 텍스트 중복과 달리 id 기반이다.
    counts = iter([3, 5])
    obj.max_id = lambda room: next(counts)
    poll = obj.poll_room('me', limit=1)
    assert next(poll)['_id'] == 4
    assert next(poll)['_id'] == 5
    poll.close()


def test_room_resolution_no_user_sql():
    obj = object.__new__(db.Database)
    obj._rooms = lambda: [{'id': 42, 'type': 'MemoChat'}]
    assert obj.resolve('me')['id'] == 42
    assert obj.resolve('room:42')['id'] == 42
    with pytest.raises(db.DBUnavailable): obj.resolve("' OR 1=1 --")
    with pytest.raises(db.DBUnavailable): obj.resolve(None)


def test_unknown_types_and_sender_row_key():
    obj = object.__new__(db.Database)
    obj.self_id, obj.names = 123, {}
    row = {'v': json.dumps({'enc': 31, 'isMine': True}), 'user_id': 123,
           'message': encrypt(123, 31, '가짜'), 'type': 999, 'created_at': 7, '_id': 1, 'id': 9}
    room = {'link_id': 0, 'members': '[]', 'watermarks': '[]'}
    msg = obj._decode(row, room)
    assert msg['kind'] == 'unknown(999)' and msg['text'] == '가짜' and msg['is_mine'] is True


@pytest.mark.parametrize('value,default', [('[]', {}), ('{}', []), ('"fake"', {})])
def test_json_shape_changes_are_fallback_errors(value, default):
    with pytest.raises(db.DBUnavailable): db.parse_json(value, default)
    assert db.parse_json('null', default) == default


def test_tui_db_room_id_cannot_send_via_ambiguous_title(monkeypatch):
    from kakao_cli.worker import Worker
    monkeypatch.setattr(receiver.consolelock, 'check', lambda dev: None)
    class Ambiguous:
        def resolve(self, target):
            if target == 'room:1': return {'id': 1}
            raise db.DBUnavailable('동명이어서 고유하게 찾지 못함')
        def room_name(self, room): return '가짜 동명 방'
    w = Worker(object())
    w.room = 'room:1'
    w.receiver = SimpleNamespace(db=Ambiguous())
    def unexpected(*args): raise AssertionError('송신 UI에 도달하면 안 됨')
    w.d._open_target = unexpected
    w.d.send_in_room = unexpected
    with pytest.raises(db.DBUnavailable): w._handle(('send', '가짜 전송 요청'))


def test_ui_dump_never_creates_body_file(monkeypatch):
    from kakao_cli.adb import Device
    from kakao_cli.errors import AdbUnavailable
    commands = []
    def run(self, args):
        commands.append(args)
        return '<?xml version="1.0"?><hierarchy><node text="가짜"/></hierarchy>\nUI hierchary dumped to: /dev/tty'
    monkeypatch.setattr(Device, '_run', run)
    assert Device('fake').dump().endswith('</hierarchy>')
    assert commands == [['exec-out', 'uiautomator dump /dev/tty']]
    monkeypatch.setattr(Device, '_run', lambda self, args: '가짜 오류')
    with pytest.raises(AdbUnavailable): Device('fake').dump()


def test_ui_fallback_does_not_return_success_without_chat_log(monkeypatch):
    from kakao_cli.errors import KakaoError
    monkeypatch.setattr(receiver.consolelock, 'check', lambda dev: None)
    monkeypatch.setattr(receiver.Driver, '_open_target', lambda *args: None)
    monkeypatch.setattr(receiver.Driver, '_screen_where', lambda *args, **kwargs: None)
    with pytest.raises(KakaoError): receiver.Receiver(object(), 'ui').read_detailed(None)

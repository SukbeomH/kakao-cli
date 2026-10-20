"""실제 본문·이름을 쓰지 않는 타입·프로필 회귀 계약."""
import json
from types import SimpleNamespace

import pytest
from kakao_cli import kakaodb as db


@pytest.mark.parametrize('raw,kind', [
    (0, 'system'), (1, 'text'), (2, 'photo'), (3, 'video'),
    (12, 'sticker'), (18, 'file'), (20, 'animated_sticker'),
    (25, 'animated_sticker_ex'), (26, 'reply'), (27, 'multi_photo'),
    (71, 'leverage'), (72, 'alimtalk'),
])
def test_observed_types_from_2683_primary_enum(raw, kind):
    assert db.message_type(raw)['kind'] == kind


@pytest.mark.parametrize('raw,base,original', [
    (16385, 1, 'text'), (16386, 2, 'photo'), (16410, 26, 'reply'),
])
def test_deletion_flag_is_not_a_new_unknown_type(raw, base, original):
    info = db.message_type(raw)
    assert info['kind'] == 'deleted' and info['is_deleted'] is True
    assert info['base_type'] == base and info['base_kind'] == original


def test_unknown_raw_and_unknown_future_flags_stay_unknown():
    for raw in (999, -999, (1 << 29) | 1):
        assert db.message_type(raw)['kind'] == 'unknown(' + str(raw) + ')'
    assert db.message_type(-11)['kind'] == 'deleted'
    assert db.message_type(-13)['kind'] == 'blinded'
    assert db.message_type(32768 | 1)['is_blinded'] is True
    assert db.message_type((1 << 28) | 1)['is_secret'] is True


def test_open_link_authoritative_name_before_incomplete_member_fallback():
    obj = object.__new__(db.Database)
    obj.self_id, obj.names = 123, {}
    obj.open_links = {456: '가짜 공개 방'}
    room = {'id': 789, 'type': 'OM', 'link_id': 456, 'private_meta': '{}', 'meta': '[]', 'members': '[7890]'}
    assert obj.room_name(room) == '가짜 공개 방'
    room['private_meta'] = '{"name":"가짜 사용자 지정 이름"}'
    assert obj.room_name(room) == '가짜 사용자 지정 이름'
    room['private_meta'], room['type'] = '{}', 'DirectChat'
    assert obj.room_name(room) == 'room:789'


def test_optional_open_link_schema_and_duplicate_ids_fail_closed():
    obj = object.__new__(db.Database)
    obj.self_id, obj.names, obj.open_links = None, {}, {}
    rows = [{'id': 1, 'name': '가짜 이름'}, {'id': 1, 'name': '가짜 다른 이름'},
            {'id': 2, 'name': '가짜 유일 이름'}, {'id': 3, 'name': ''}]
    obj._query = lambda database, sql: ([{'name': 'id'}, {'name': 'name'}]
                                       if sql.startswith('PRAGMA') else rows)
    obj._load_open_links()
    assert obj.open_links == {2: '가짜 유일 이름'}
    obj._query = lambda *args: []
    obj._load_open_links()
    assert obj.open_links == {}


def test_sqlcipher_error_in_exec_out_stdout_never_looks_like_empty_success(monkeypatch):
    monkeypatch.setattr(db.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='Error: file is not a database\n'))
    obj = object.__new__(db.Database)
    obj.dev = SimpleNamespace(adb='fake-adb', serial='fake')
    with pytest.raises(db.DBUnavailable): obj._query('KakaoTalk.db', 'SELECT 1')


@pytest.mark.parametrize('raw,kind', [(0, 'system'), (18, 'file'), (26, 'reply')])
def test_detailed_rows_keep_body_and_add_confirmed_kind(monkeypatch, raw, kind):
    obj = object.__new__(db.Database)
    obj.self_id, obj.names = 123, {}
    calls = []
    def fake_decrypt(user, enc, value):
        calls.append((user, enc))
        return '직접 만든 가짜 본문'
    monkeypatch.setattr(db, 'decrypt', fake_decrypt)
    row = {'type': raw, 'v': '{"enc":31,"isMine":true}', 'user_id': 123,
           'message': '가짜 암호문', 'created_at': 1, '_id': 1, 'id': 1}
    room = {'link_id': 0, 'members': '[]', 'watermarks': '[]'}
    detail = obj._decode(row, room)
    assert detail['kind'] == kind and detail['type'] == raw
    assert detail['text'] == '직접 만든 가짜 본문' and calls == [(123, 31)]
    assert not detail['is_deleted']


@pytest.mark.parametrize('raw,expected', [(16385, '[Deleted]'), (16386, '[Deleted]'),
                                         (16410, '[Deleted]'), (-11, '[Deleted]'),
                                         (32769, '[Blinded]'), (-13, '[Blinded]')])
def test_deleted_or_blinded_rows_never_resurrect_remaining_plaintext(monkeypatch, raw, expected):
    obj = object.__new__(db.Database)
    obj.self_id, obj.names = 123, {}
    def unexpected(*args): raise AssertionError('삭제·가림 본문을 복호화하면 안 됨')
    monkeypatch.setattr(db, 'decrypt', unexpected)
    row = {'type': raw, 'v': '{}', 'user_id': 123, 'message': '남아 있는 가짜 암호문',
           'created_at': 1, '_id': 1, 'id': 1}
    room = {'link_id': 0, 'members': '[]', 'watermarks': '[]'}
    assert obj._decode(row, room)['text'] == expected


def test_optional_open_link_failure_keeps_existing_receive_path():
    obj = object.__new__(db.Database)
    obj.open_links = {}
    def broken(*args): raise db.DBUnavailable('가짜 선택 메타 실패')
    obj._query = broken
    obj._load_open_links()
    assert obj.open_links == {}


@pytest.mark.parametrize('unsafe', [
    {'sender': '직접 만든 가짜 이름'},
    {'message_rows': '직접 만든 가짜 본문'},
    {'kinds': {'직접 만든 가짜 이름': 1}},
])
def test_live_verifier_rejects_unapproved_output_fields(unsafe):
    from verify_profile_types_live import aggregate_allowed
    # 정상 집계 전체를 먼저 허용한 뒤, 한 위치에만 비허용 값을 넣는다.
    safe = {
        'room_count': 2, 'before_named_rooms': 0, 'after_named_rooms': 1,
        'open_link_names': 1, 'existing_member_names': 1,
        'message_rows': 2, 'decoded_rows': 2, 'decode_failed': 0,
        'body_rows_excluding_removed': 2, 'body_ok_excluding_removed': 2,
        'deleted_rows': 0, 'blinded_rows': 0, 'unknown_rows': 0,
        'unchanged_text_for_unmasked_rows': True, 'is_mine_matches_baseline': True,
        'device_has_sqlcipher': False, 'live_service_start_times_unchanged': True,
        'kinds': {'text': 2}, 'type_distribution': {'1': 2},
        'crypto_user_probe': {'plain_query_valid': False, 'not_a_database_error': True, 'has_cipher_version': False},
        'crypto_probe': {'plain_query_valid': False, 'not_a_database_error': True, 'has_cipher_version': False},
    }
    assert aggregate_allowed(safe) is True
    assert aggregate_allowed({**safe, **unsafe}) is False

"""기기 내부 SQLite 읽기 전용 수신. DB/복호화 결과를 파일에 쓰지 않는다.

암호 형식 참고: https://github.com/jiru/kakaodecrypt (WTFPL).
PKCS#12 SHA-1/AES-CBC 형식을 cryptography로 자체 구현. Iris 코드 사용 없음.
아래 PASSWORD/IV는 공개 파일 형식 상수이며 계정 자격증명이 아니다.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import hashlib
import json
import re
import shlex
import subprocess
import time

from .errors import KakaoError

VERSION_CODE = 29260830
ROOT = '/data/data/com.kakao.talk/databases/'
PREFIXES = ('', '', '12', '24', '18', '30', '36', '12', '48', '7', '35',
            '40', '17', '23', '29', 'isabel', 'kale', 'sulli', 'van', 'merry',
            'kyle', 'james', 'maddux', 'tony', 'hayden', 'paul', 'elijah',
            'dorothy', 'sally', 'bran', 'extr.ursra', 'veil')
PASSWORD = bytes.fromhex('1608096f02172b0821210a1003030706')
IV = bytes.fromhex('0f080100194725dc15f517e0e1150c35')
# 실측 type 1/2/3과 UI 비교로 확인한 종류만. 나머지는 추측하지 않는다.
KINDS = {1: 'text', 2: 'photo', 3: 'video'}


class DBUnavailable(KakaoError):
    code = 'db_unavailable'


def number(value):
    """SQL에 들어가는 값은 64비트 정수만 허용. bool/문자열도 거절한다."""
    if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
        raise DBUnavailable('DB 조회 인자는 64비트 정수여야 합니다')
    return str(value)


def salt(user_id: int, enc: int) -> bytes:
    if type(enc) is not int or not 0 <= enc < len(PREFIXES):
        raise DBUnavailable('지원하지 않는 DB enc 형식입니다')
    if type(user_id) is not int:
        raise DBUnavailable('잘못된 DB 발신자 형식입니다')
    return (PREFIXES[enc] + str(user_id))[:16].ljust(16, '\0').encode() if user_id > 0 else bytes(16)


@lru_cache(maxsize=2048)
def derive_key(s: bytes) -> bytes:
    """PKCS#12 id=1, SHA-1, iterations=2, 32바이트 키."""
    p = (PASSWORD.decode('ascii') + '\0').encode('utf-16-be')
    material = s * 4 + (p * ((64 + len(p) - 1) // len(p)))[:64]
    out = b''
    for _ in range(2):
        a = hashlib.sha1(hashlib.sha1(bytes([1]) * 64 + material).digest()).digest()
        out += a
        b = (a * 4)[:64]
        increment = int.from_bytes(b, 'big') + 1
        material = b''.join(((int.from_bytes(material[i:i+64], 'big') + increment)
                             % (1 << 512)).to_bytes(64, 'big')
                            for i in range(0, len(material), 64))
    return out[:32]


def decrypt(user_id: int, enc: int, value: str | None) -> str:
    if not value:
        return ''
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
        key = derive_key(salt(user_id, enc))
        raw = base64.b64decode(value, validate=True)
        ctx = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
        padded = ctx.update(raw) + ctx.finalize()
        unpad = PKCS7(128).unpadder()
        return (unpad.update(padded) + unpad.finalize()).decode('utf-8')
    except Exception:
        # 라이브 ciphertext/본문/이름을 예외나 로그로 유출하지 않는다.
        raise DBUnavailable('DB 복호화 실패: UI 수신 경로가 필요합니다') from None


def parse_json(value, default):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if parsed is None:
            return default
        if not isinstance(parsed, type(default)):
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise DBUnavailable('DB JSON 형식이 변경되었습니다') from None


def unread_count(log_id, sender, members, watermarks, self_id):
    """members[i]의 최종 읽은 id=watermarks[i]. 발신자/자신 제외, < log_id 합.

    메모방은 0. 배열 불일치/잘못된 값이면 추정하지 않고 None.
    """
    if self_id is None and members:
        return None
    if not isinstance(members, list) or not isinstance(watermarks, list) or len(members) != len(watermarks):
        return None
    if any(type(v) is not int for v in members + watermarks):
        return None
    return sum(mark < log_id for member, mark in zip(members, watermarks)
               if member not in (sender, self_id))


class Database:
    def __init__(self, dev):
        self.dev = dev
        self.self_id = None
        self.names = {}
        self.guard()
        own = self._query('KakaoTalk.db',
            "SELECT DISTINCT user_id FROM chat_logs WHERE json_extract(v,'$.isMine')=1")
        if len(own) == 1:
            self.self_id = own[0]['user_id']
        self._load_names()

    def _query(self, db, sql):
        if db not in ('KakaoTalk.db', 'KakaoTalk2.db'):
            raise DBUnavailable('허용되지 않은 DB입니다')
        # SQL은 이 모듈의 고정 템플릿과 number()만 사용. 사용자 이름은 SQL에 넣지 않는다.
        cmd = 'sqlite3 -readonly -json ' + shlex.quote(ROOT + db) + ' ' + shlex.quote(sql)
        try:
            p = subprocess.run([self.dev.adb, '-s', self.dev.serial, 'exec-out', cmd],
                               capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            if p.returncode:
                raise ValueError()
            result = json.loads(p.stdout or '[]')
            if not isinstance(result, list):
                raise ValueError()
            return result
        except (OSError, ValueError, subprocess.SubprocessError):
            raise DBUnavailable('기기 DB 읽기 실패(sqlite3 -readonly -json 필요)') from None

    def guard(self):
        out = self.dev.shell('dumpsys package com.kakao.talk | grep versionCode')
        match = re.search(r'\bversionCode=(\d+)\b', out)
        if not match or int(match[1]) != VERSION_CODE:
            raise DBUnavailable('검증되지 않은 앱 versionCode: UI 수신으로 폴백합니다')
        required = {
            'chat_logs': {'_id', 'id', 'type', 'chat_id', 'user_id', 'message', 'attachment', 'created_at', 'v'},
            'chat_rooms': {'id', 'type', 'members', 'active_member_ids', 'watermarks', 'last_read_log_id',
                           'meta', 'private_meta', 'link_id', 'unread_count', 'mute_until_at', 'last_updated_at'}}
        for table, cols in required.items():
            found = {r['name'] for r in self._query('KakaoTalk.db', 'PRAGMA table_info(' + table + ')')}
            if not cols <= found:
                raise DBUnavailable('DB 필수 열이 변경되었습니다: UI 수신으로 폴백합니다')

    def _load_names(self):
        # 프로필 암호화는 현재 계정 id를 우선 사용. 행 user_id는 채팅 본문 키에만 사용한다.
        if self.self_id is None:
            return
        for table, field, ident, required in (
                ('friends', 'name', 'id', {'id', 'name', 'enc'}),
                ('open_chat_member', 'nickname', 'user_id', {'link_id', 'user_id', 'nickname', 'enc'})):
            cols = {r['name'] for r in self._query('KakaoTalk2.db', 'PRAGMA table_info(' + table + ')')}
            if not required <= cols:
                continue
            for row in self._query('KakaoTalk2.db', 'SELECT * FROM ' + table):
                try:
                    name = decrypt(self.self_id, row['enc'], row[field])
                    if name:
                        self.names[(row.get('link_id', 0), row[ident])] = name
                except DBUnavailable:
                    continue  # 신규 프로필 미지원: 식별자로 표시, 잘못된 이름 추측 금지

    def _sender(self, user_id, link_id=0):
        if user_id == self.self_id:
            return '나'
        return self.names.get((link_id, user_id), self.names.get((0, user_id), 'user:' + str(user_id)))

    def _rooms(self):
        return self._query('KakaoTalk.db', 'SELECT * FROM chat_rooms ORDER BY last_updated_at DESC')

    def room_name(self, r):
        if r['type'] == 'MemoChat':
            return 'me'
        private = parse_json(r['private_meta'], {})
        if isinstance(private, dict) and isinstance(private.get('name'), str) and private['name']:
            return private['name']
        # meta type=1은 방 이름. content가 JSON 객체이면 name만 사용한다.
        for item in parse_json(r['meta'], []):
            if isinstance(item, dict) and item.get('type') == 1:
                content = item.get('content')
                if isinstance(content, str) and content:
                    return content
        members = parse_json(r['members'], [])
        names = [self._sender(m, r['link_id']) for m in members if m != self.self_id]
        if names and all(not n.startswith('user:') for n in names):
            return ', '.join(names)
        return 'room:' + str(r['id'])

    def resolve(self, target):
        if target is None:
            raise DBUnavailable('DB 수신에는 방 이름, me 또는 room:<id>를 지정하세요')
        rows = self._rooms()
        if str(target).lower() in ('me', 'self', '나', '나와의 채팅'):
            matches = [r for r in rows if r['type'] == 'MemoChat']
        else:
            matches = [r for r in rows if target == 'room:' + str(r['id']) or target == self.room_name(r)]
        if len(matches) != 1:
            raise DBUnavailable('DB 방을 고유하게 찾지 못했습니다. rooms의 room:<id>를 사용하세요')
        return matches[0]

    def list_rooms(self):
        result = []
        for r in self._rooms():
            last = self._query('KakaoTalk.db',
                'SELECT * FROM chat_logs WHERE chat_id=' + number(r['id']) + ' ORDER BY _id DESC LIMIT 1')
            detail = self._decode(last[0], r) if last else None
            result.append({'name': self.room_name(r), 'room_id': r['id'], 'target': 'room:' + str(r['id']),
                           'last_message': detail['text'] if detail else '',
                           'time': detail['time'] if detail else '',
                           'members_count': len(parse_json(r['members'], [])) + 1,
                           'unread': r['unread_count'], 'mute_until_at': r['mute_until_at'], 'source': 'db'})
        return result

    def _decode(self, row, room):
        v = parse_json(row['v'], {})
        text = decrypt(row['user_id'], v.get('enc'), row['message'])
        kind = KINDS.get(row['type'], 'unknown(' + str(row['type']) + ')')
        if not text and kind in ('photo', 'video'):
            text = '[Photo]' if kind == 'photo' else '[Video]'
        return {'text': text, 'kind': kind, 'type': row['type'], 'sender': self._sender(row['user_id'], room['link_id']),
                'user_id': row['user_id'], 'is_mine': v.get('isMine') in (True, 1, 'true'),
                'created_at': row['created_at'], 'time': row['created_at'],
                'unread_count': unread_count(row['id'], row['user_id'], parse_json(room['members'], []),
                                             parse_json(room['watermarks'], []), self.self_id),
                '_id': row['_id'], 'id': row['id'], 'source': 'db'}

    def read_detailed(self, target, limit=20):
        n = number(limit)
        if limit < 1:
            raise DBUnavailable('limit은 1 이상이어야 합니다')
        room = self.resolve(target)
        rows = self._query('KakaoTalk.db', 'SELECT * FROM chat_logs WHERE chat_id=' + number(room['id'])
                           + ' ORDER BY _id DESC LIMIT ' + n)
        return [self._decode(r, room) for r in reversed(rows)]

    def max_id(self, room_id):
        rows = self._query('KakaoTalk.db', 'SELECT MAX(_id) AS n FROM chat_logs WHERE chat_id=' + number(room_id))
        return rows[0]['n'] or 0

    def changes(self, target, after, limit=100):
        if after < 0 or limit < 1:
            raise DBUnavailable('증분 커서/limit 범위가 잘못되었습니다')
        room = self.resolve(target)
        rows = self._query('KakaoTalk.db', 'SELECT * FROM chat_logs WHERE chat_id=' + number(room['id'])
                           + ' AND _id>' + number(after) + ' ORDER BY _id LIMIT ' + number(limit))
        return [self._decode(r, room) for r in rows]

    def poll_room(self, target, interval=3., limit=100):
        if interval <= 0 or limit < 1:
            raise DBUnavailable('interval/limit은 양수여야 합니다')
        room = self.resolve(target)
        cursor = self.max_id(room['id'])
        while True:
            self.guard()
            if self.max_id(room['id']) > cursor:
                while batch := self.changes(target, cursor, limit):
                    for msg in batch:
                        yield {'room': target, **msg}
                        cursor = msg['_id']
            time.sleep(interval)

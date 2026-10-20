"""수동 실기 검증: VM에서 PYTHONPATH=src python3 tests/verify_db_live.py.

송신 없음. DB 반출/본문·이름 출력/스크린샷 저장 없음.
--ui는 콘솔 잠금이 없을 때 자기채팅을 열어 메모리 dump와 비교한다.
"""
import argparse
import json
import uuid
from types import SimpleNamespace
from kakao_cli import cli, consolelock
from kakao_cli.adb import Device
from kakao_cli.driver import Driver
from kakao_cli.kakaodb import Database, DBUnavailable


def verify(dev, compare_ui=False):
    result = {}
    database = Database(dev)
    rows = database._query('KakaoTalk.db', 'SELECT * FROM chat_logs')
    success = failed = body = 0
    from kakao_cli.kakaodb import decrypt
    for row in rows:
        if not row['message']:
            continue
        body += 1
        try:
            decrypt(row['user_id'], json.loads(row['v'])['enc'], row['message'])
            success += 1
        except DBUnavailable:
            failed += 1
    result.update(rows=len(rows), body_rows=body, decrypted=success, decrypt_failed=failed)
    rooms = database.list_rooms()
    result.update(rooms=len(rooms), profile_names=len(database.names),
                  rooms_with_title=sum(not r['name'].startswith('room:') for r in rooms))
    room = database.resolve('me')
    recent = database.read_detailed('me', 20)
    cursor = database.max_id(room['id'])
    result.update(self_recent_count=len(recent), self_all_is_mine=all(m['is_mine'] for m in recent),
                  after_max_empty=not database.changes('me', cursor),
                  previous_cursor_detects_latest=bool(cursor and database.changes('me', cursor - 1)[-1]['_id'] == cursor))
    if compare_ui:
        consolelock.check(dev)
        driver = Driver(dev)
        driver.open_self_chat()
        visible = [m for m in driver._messages_detailed(driver.screen()) if m['kind'] == 'text']
        dbtext = [m['text'] for m in database.read_detailed('me', 100) if m['kind'] == 'text']
        texts = [m['text'] for m in visible]
        result.update(visible_text_count=len(texts),
                      ui_db_text_match=bool(texts and any(dbtext[i:i+len(texts)] == texts
                                           for i in range(len(dbtext)-len(texts)+1))),
                      ui_all_is_mine=all(m.get('is_mine') for m in visible))
    # 기존 파일이 있으면 실제 소유자의 잠금을 보존한다. 빈 자리에만 잠금을 생성한다.
    token = 'dbverify_' + uuid.uuid4().hex
    lock = consolelock.LOCK
    acquired = dev.shell(f"(set -C; echo $(date +%s) {token} 0 virtual > {lock}) 2>/dev/null && echo acquired").strip() == 'acquired'
    try:
        active = consolelock.parse(dev.shell(f'cat {lock} 2>/dev/null; echo ==; date +%s'))[0]
        result['real_console_lock_active'] = active
        if active:
            original = cli._dev
            try:
                cli._dev = lambda args: dev
                args = SimpleNamespace(source='db', room='me', limit=20)
                result['db_read_during_lock'] = cli.cmd_read(args)['count'] > 0
                args.source = 'ui'
                try:
                    cli.cmd_read(args)
                    result['ui_rejected_during_lock'] = False
                except Exception as error:
                    result['ui_rejected_during_lock'] = type(error).__name__ == 'ConsoleBusy'
            finally:
                cli._dev = original
    finally:
        if acquired:
            # 콘솔이 이미 새 잠금으로 바꿨다면 지우지 않는다.
            dev.shell(f"grep -q ' {token} ' {lock} && rm -f {lock}")
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--serial', default='127.0.0.1:5555')
    ap.add_argument('--ui', action='store_true')
    args = ap.parse_args()
    try:
        print(json.dumps(verify(Device(args.serial), args.ui)))
    except Exception:
        print(json.dumps({'verification_failed': True}))
        raise SystemExit(1)

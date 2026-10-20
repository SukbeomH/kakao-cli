"""기기 읽기 전용 실측. 운영 배포 경로에 파일을 쓰거나 UI를 조작하지 않는다.

로컬 실행: uv run --python .venv/bin/python tests/verify_profile_types_live.py
--host 기본 agent@192.168.0.30. 기준/후보 소스를 SSH stdin으로 메모리에만 적재한다.
출력은 개수·불리언·공개 타입 번호와 라벨뿐이다. 키 파일에 접근하지 않고 DB 사본을 만들지 않는다.
"""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

BASELINE = '989301ae5c47ab332762de0c68a3acfd87c64342'
ROOT = Path(__file__).resolve().parents[1]

REMOTE = r'''
import collections,json,shlex,subprocess,sys,types
pkg=types.ModuleType('kakao_cli');pkg.__path__=[];sys.modules['kakao_cli']=pkg
for name,source in sources.items():
    module=types.ModuleType('kakao_cli.'+name);module.__package__='kakao_cli'
    sys.modules[module.__name__]=module
    exec(compile(source,'<read-only-review:'+name+'>','exec'),module.__dict__)
base=sys.modules['kakao_cli.baseline_db'];new=sys.modules['kakao_cli.kakaodb']
# 과거 후속5의 평문 메타/타입 비교 범위: SQLCipher 선택 경로는 실행하지 않는다.
if hasattr(new.Database,'_load_crypto_profiles'):
    new.Database._load_crypto_profiles=lambda self: None
class ReadOnlyDevice:
    adb='adb';serial='127.0.0.1:5555'
    def shell(self,command):
        # Database.guard가 사용하는 버전 조회 외의 셸/UI 명령을 거절한다.
        if command!='dumpsys package com.kakao.talk | grep versionCode':
            raise RuntimeError('non_readonly_device_command')
        return subprocess.run(['adb','-s',self.serial,'shell',command],capture_output=True,text=True,timeout=30).stdout

def plain_probe(database,sql):
    if database not in ('crypto_user_database','crypto_database','KakaoTalk.db'):
        raise RuntimeError('unexpected_database')
    if sql not in ('SELECT count(*) AS n FROM sqlite_master','PRAGMA cipher_version'):
        raise RuntimeError('unexpected_sql')
    command='sqlite3 -readonly -json '+shlex.quote('/data/data/com.kakao.talk/databases/'+database)+' '+shlex.quote(sql)
    p=subprocess.run(['adb','-s','127.0.0.1:5555','exec-out',command],capture_output=True,text=True,timeout=30)
    try: rows=json.loads(p.stdout); valid=isinstance(rows,list)
    except Exception: valid=False
    return {'plain_query_valid':valid,'not_a_database_error':'not a database' in (p.stdout+p.stderr).lower(),'has_cipher_version':sql=='PRAGMA cipher_version' and bool(p.stdout.strip())}

try:
    device=ReadOnlyDevice();old=base.Database(device);candidate=new.Database(device)
    rooms=candidate._rooms();by_id={r['id']:r for r in rooms}
    result={'room_count':len(rooms),'before_named_rooms':sum(not old.room_name(r).startswith('room:') for r in rooms),
            'after_named_rooms':sum(not candidate.room_name(r).startswith('room:') for r in rooms),
            'open_link_names':len(candidate.open_links),'existing_member_names':len(candidate.names)}
    rows=candidate._query('KakaoTalk.db','SELECT * FROM chat_logs')
    kinds=collections.Counter();dist=collections.Counter();failed=deleted=blinded=body=ok=0;unchanged=True;mine_match=True
    for row in rows:
        dist[row['type']]+=1
        try:
            detail=candidate._decode(row,by_id[row['chat_id']]);kinds[detail['kind']]+=1
            deleted+=detail['is_deleted'];blinded+=detail['is_blinded']
            if detail['is_deleted'] or detail['is_blinded']:
                if detail['text'] not in ('[Deleted]','[Blinded]'): raise RuntimeError('unmasked_removed_body')
            else:
                before=old._decode(row,by_id[row['chat_id']])
                unchanged=unchanged and detail['text']==before['text']
                mine_match=mine_match and detail['is_mine']==before['is_mine']
                if row['message']: body+=1;ok+=1
        except Exception: failed+=1
    result.update(message_rows=len(rows),decoded_rows=sum(kinds.values()),decode_failed=failed,
                  body_rows_excluding_removed=body,body_ok_excluding_removed=ok,deleted_rows=deleted,blinded_rows=blinded,
                  unchanged_text_for_unmasked_rows=unchanged,is_mine_matches_baseline=mine_match,
                  unknown_rows=sum(n for k,n in kinds.items() if k.startswith('unknown(')),
                  kinds=dict(sorted(kinds.items())),type_distribution=dict(sorted(dist.items())))
    result['crypto_user_probe']=plain_probe('crypto_user_database','SELECT count(*) AS n FROM sqlite_master')
    result['crypto_probe']=plain_probe('crypto_database','SELECT count(*) AS n FROM sqlite_master')
    result['device_has_sqlcipher']=plain_probe('KakaoTalk.db','PRAGMA cipher_version')['has_cipher_version']
    baseline={'/kakao-redroid-1':'2026-10-20T22:05:07.273168734Z','/kakao-webui-1':'2026-10-20T22:09:44.352028438Z'}
    p=subprocess.run(['docker','inspect','kakao-redroid-1','kakao-webui-1','--format','{{.Name}} {{.State.StartedAt}} {{.RestartCount}}'],capture_output=True,text=True,timeout=30)
    states=[line.split() for line in p.stdout.splitlines()]
    result['live_service_start_times_unchanged']=len(states)==2 and all(len(s)==3 and baseline.get(s[0])==s[1] and s[2]=='0' for s in states)
    print(json.dumps(result))
    if failed or not unchanged or not mine_match or not result['live_service_start_times_unchanged']: raise SystemExit(1)
except Exception:
    print(json.dumps({'verification_failed':True}));raise SystemExit(1)
'''


COUNT_FIELDS = {'room_count', 'before_named_rooms', 'after_named_rooms', 'open_link_names',
                'existing_member_names', 'message_rows', 'decoded_rows', 'decode_failed',
                'body_rows_excluding_removed', 'body_ok_excluding_removed', 'deleted_rows',
                'blinded_rows', 'unknown_rows'}
BOOL_FIELDS = {'unchanged_text_for_unmasked_rows', 'is_mine_matches_baseline',
               'device_has_sqlcipher', 'live_service_start_times_unchanged'}
PROBE_FIELDS = {'plain_query_valid', 'not_a_database_error', 'has_cipher_version'}
PUBLIC_KINDS = {'system', 'text', 'photo', 'video', 'sticker', 'file', 'animated_sticker',
                'animated_sticker_ex', 'reply', 'multi_photo', 'leverage', 'alimtalk',
                'deleted', 'blinded'}


def aggregate_allowed(result):
    """본문/이름이 실수로 JSON에 들어와도 출력하지 않는 독립 허용목록."""
    if type(result) is not dict:
        return False
    expected = COUNT_FIELDS | BOOL_FIELDS | {'kinds', 'type_distribution', 'crypto_user_probe', 'crypto_probe'}
    if set(result) != expected:
        return False
    if any(type(result[key]) is not int or result[key] < 0 for key in COUNT_FIELDS):
        return False
    if any(type(result[key]) is not bool for key in BOOL_FIELDS):
        return False
    for key in ('crypto_user_probe', 'crypto_probe'):
        probe = result[key]
        if type(probe) is not dict or set(probe) != PROBE_FIELDS or any(type(v) is not bool for v in probe.values()):
            return False
    for field in ('kinds', 'type_distribution'):
        if type(result[field]) is not dict:
            return False
        for key, count in result[field].items():
            if type(key) is not str or type(count) is not int or count < 0:
                return False
            if field == 'kinds' and key not in PUBLIC_KINDS and not re.fullmatch(r'unknown\(-?[0-9]+\)', key):
                return False
            if field == 'type_distribution' and not re.fullmatch(r'-?[0-9]+', key):
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='agent@192.168.0.30')
    args = ap.parse_args()
    old = subprocess.run(['git', 'show', BASELINE + ':src/kakao_cli/kakaodb.py'],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    sources = {'errors': (ROOT/'src/kakao_cli/errors.py').read_text(), 'baseline_db': old,
               'kakaodb': (ROOT/'src/kakao_cli/kakaodb.py').read_text()}
    # 소스에 비밀을 포함하지 않는다. 개인 데이터는 원격 프로세스 메모리에만 둔다.
    payload = 'sources=' + repr(sources) + '\n' + REMOTE
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', args.host, 'python3', '-'],
                       input=payload, text=True, capture_output=True, timeout=180)
    # 외부 stderr/traceback을 출력하지 않고 집계 JSON만 출력한다.
    for line in p.stdout.splitlines():
        try:
            result = json.loads(line)
        except Exception:
            print(json.dumps({'invalid_probe_output': True}))
            return 1
        if not aggregate_allowed(result):
            print(json.dumps({'invalid_probe_output': True}))
            return 1
        print(json.dumps(result))
    if p.stderr:
        print(json.dumps({'remote_stderr_present': True}))
        return 1
    if not p.stdout.strip():
        print(json.dumps({'empty_probe_output': True}))
        return 1
    return p.returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print(json.dumps({'verification_failed': True}))
        sys.exit(1)

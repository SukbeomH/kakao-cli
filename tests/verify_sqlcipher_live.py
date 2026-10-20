"""실기 SQLCipher 검증: 소스는 SSH stdin/메모리, 배포 파일 수정 없음.

기기 helper 임시 경로만 생성/정리한다. 성공/실패 모두 집계·불리언만 출력한다.
공식 AAR와 고정 helper jar를 VM ~/.cache/kakao-cli/sqlcipher-4.10.0/에 먼저 준비한다.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

BASELINE='81fe2af8311f4443751d4f1c350563584f103363'
ROOT=Path(__file__).resolve().parents[1]
REMOTE=r'''
import sys,types,json,subprocess
pkg=types.ModuleType('kakao_cli');pkg.__path__=[];sys.modules['kakao_cli']=pkg
for name,source in sources.items():
 m=types.ModuleType('kakao_cli.'+name);m.__package__='kakao_cli';sys.modules[m.__name__]=m
 exec(compile(source,'<sqlcipher-review:'+name+'>','exec'),m.__dict__)
profiles=sys.modules['kakao_cli.profiles'];base=sys.modules['kakao_cli.baseline_db'];new=sys.modules['kakao_cli.kakaodb']
class Dev:
 adb='adb';serial='127.0.0.1:5555'
 def shell(self,command):
  if command!='dumpsys package com.kakao.talk | grep versionCode':raise RuntimeError()
  return subprocess.run([self.adb,'-s',self.serial,'shell',command],capture_output=True,text=True,timeout=30).stdout
observed={};original=profiles._run

def run(dev,command,timeout=45):
 p=original(dev,command,timeout)
 if 'app_process' in command:
  try:
   o=json.loads(p.stdout);observed['engine_json']=True
   observed['opened_readonly']=o.get('ok') is True and o.get('read_only') is True
   for field,values in [('stage',['isolation','library','preferences','open_readonly','schema','query','close']),('kind',['security','sqlite','reflection','native','other'])]:
    for value in values:
     if o.get(field)==value:observed['failed_'+field+'_'+value]=True
  except Exception:observed['engine_json']=False
  observed['engine_stderr_present']=bool(p.stderr)
 return p
profiles._run=run
try:
 d=Dev();before=base.Database(d);after=new.Database(d);rooms=after._rooms()
 observed.update(profile_available=after.crypto_available,users=len(after.crypto_users),
  channels=sum(len(v) for v in after.crypto_channels.values()),room_count=len(rooms),
  before_named_rooms=sum(not before.room_name(r).startswith('room:') for r in rooms),
  after_named_rooms=sum(not after.room_name(r).startswith('room:') for r in rooms))
 rows=after._query('KakaoTalk.db','SELECT * FROM chat_logs');by_id={r['id']:r for r in rooms}
 unchanged=True;mine=True;failed=0;sender_before=sender_after=0
 for row in rows:
  try:
   a=after._decode(row,by_id[row['chat_id']]);b=before._decode(row,by_id[row['chat_id']])
   unchanged=unchanged and a['text']==b['text'] and a['kind']==b['kind']
   mine=mine and a['is_mine']==b['is_mine']
   sender_before+=not b['sender'].startswith('user:');sender_after+=not a['sender'].startswith('user:')
  except Exception:failed+=1
 observed.update(message_rows=len(rows),decode_failed=failed,unchanged_body_and_type=unchanged,is_mine_matches=mine,
                 before_named_sender_rows=sender_before,after_named_sender_rows=sender_after)
 p=original(d,'test ! -e '+profiles.DEVICE_DIR+' && printf clean')
 observed['helper_cleaned']=p.stdout==b'clean'
 print(json.dumps(observed))
 if not after.crypto_available or failed or not unchanged or not mine or not observed['helper_cleaned']:raise SystemExit(1)
except Exception:
 print(json.dumps({'verification_failed':True}));raise SystemExit(1)
'''


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--host',default='agent@192.168.0.30');args=ap.parse_args()
 old=subprocess.run(['git','show',BASELINE+':src/kakao_cli/kakaodb.py'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
 sources={name:(ROOT/'src/kakao_cli'/(name+'.py')).read_text() for name in ['errors','profiles']}
 sources['baseline_db']=old;sources['kakaodb']=(ROOT/'src/kakao_cli/kakaodb.py').read_text()
 p=subprocess.run(['ssh','-o','BatchMode=yes',args.host,'python3','-'],input='sources='+repr(sources)+'\n'+REMOTE,
                  capture_output=True,text=True,timeout=180)
 allowed={'engine_json','opened_readonly','engine_stderr_present','profile_available','users','channels','room_count',
          'before_named_rooms','after_named_rooms','message_rows','decode_failed','unchanged_body_and_type','is_mine_matches',
          'before_named_sender_rows','after_named_sender_rows','helper_cleaned','verification_failed'}
 allowed.update('failed_stage_'+s for s in ['isolation','library','preferences','open_readonly','schema','query','close'])
 allowed.update('failed_kind_'+s for s in ['security','sqlite','reflection','native','other'])
 if not p.stdout.strip():print(json.dumps({'empty_probe':True}));return 1
 for line in p.stdout.splitlines():
  obj=json.loads(line)
  if type(obj) is not dict or set(obj)-allowed or any(type(v) not in (bool,int) for v in obj.values()):
   print(json.dumps({'invalid_probe':True}));return 1
  print(json.dumps(obj))
 if p.stderr:print(json.dumps({'remote_stderr_present':True}));return 1
 return p.returncode


if __name__=='__main__':
 try:sys.exit(main())
 except Exception:print(json.dumps({'verification_failed':True}));sys.exit(1)

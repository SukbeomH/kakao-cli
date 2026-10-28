"""실제 VM/Docker 없이 배포 래퍼의 실행·전송 경계를 검증한다."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def stub(path, content):
    path.write_text(content)
    path.chmod(0o755)


def test_lima_wrapper_defaults_to_config_and_only_tracked_code(tmp_path):
    log = tmp_path / 'calls.jsonl'
    stub(tmp_path / 'limactl', '''#!/usr/bin/env python3
import json,os,sys,tarfile
record={'args':sys.argv[1:]}
if 'tar' in sys.argv:
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
        record['files']=[m.name for m in archive]
with open(os.environ['KAKAO_TEST_CALLS'],'a') as out:
    out.write(json.dumps(record)+'\\n')
''')
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               KAKAO_TEST_CALLS=str(log))
    p = subprocess.run(['bash', 'scripts/lima-compose.sh'], cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1]['args'][-2:] == ['config', '--quiet']
    assert not any('up' in c['args'] or 'start' in c['args'] for c in calls)
    assert '--workdir' in calls[-1]['args'] and '/opt/kakao-cli' in calls[-1]['args']
    files = next(c['files'] for c in calls if 'files' in c)
    assert 'deploy/compose.yaml' in files and 'src/kakao_cli/kakaodb.py' in files
    assert not any('/.git/' in f or '/.venv/' in f or f.endswith(('.db', '.pem', '.key'))
                   or Path(f).name.startswith('.env') for f in files)


def test_lima_wrapper_rejects_volume_deletion_before_guest_access(tmp_path):
    marker = tmp_path / 'guest-called'
    stub(tmp_path / 'limactl', '#!/bin/sh\ntouch "$KAKAO_TEST_MARKER"\n')
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               KAKAO_TEST_MARKER=str(marker))
    p = subprocess.run(['bash', 'scripts/lima-compose.sh', 'down', '-v'], cwd=ROOT,
                       env=env, capture_output=True, text=True)
    assert p.returncode == 2
    assert not marker.exists()


def test_boot_missing_apk_fails_before_launch_or_input(tmp_path):
    log = tmp_path / 'adb-calls'
    stub(tmp_path / 'adb', '''#!/bin/sh
printf '%s\\n' "$*" >> "$KAKAO_TEST_CALLS"
case "$*" in
  *'getprop sys.boot_completed'*) echo 1 ;;
esac
''')
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               KAKAO_TEST_CALLS=str(log), KAKAO_ADB_TARGET='fake:5555')
    p = subprocess.run(['bash', 'scripts/compose-boot.sh'], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert p.returncode == 1
    calls = log.read_text()
    assert 'pm path com.kakao.talk' in calls
    assert 'am start' not in calls and 'input ' not in calls


def test_boot_success_never_sends_message(tmp_path):
    log = tmp_path / 'adb-calls'
    stub(tmp_path / 'sleep', '#!/bin/sh\nexit 0\n')
    stub(tmp_path / 'adb', '''#!/bin/sh
printf '%s\\n' "$*" >> "$KAKAO_TEST_CALLS"
case "$*" in
  *'getprop sys.boot_completed'*) echo 1 ;;
  *'pm path com.kakao.talk'*) echo 'package:fake.apk' ;;
  *'ime list -a -s'*) echo 'com.android.adbkeyboard/.AdbIME' ;;
  *'dumpsys activity activities'*) echo 'topResumedActivity com.kakao.talk/FakeActivity' ;;
esac
''')
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               KAKAO_TEST_CALLS=str(log), KAKAO_ADB_TARGET='fake:5555')
    p = subprocess.run(['bash', 'scripts/compose-boot.sh'], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    inputs = [line for line in log.read_text().splitlines() if ' input ' in line]
    assert inputs == ['-s fake:5555 shell input keyevent KEYCODE_HOME']
    assert 'ADB_INPUT_TEXT' not in log.read_text() and 'tap ' not in log.read_text()


def test_lima_network_opt_in_survives_updates_and_can_be_disabled(tmp_path):
    log = tmp_path / 'calls.jsonl'
    stub(tmp_path / 'limactl', '''#!/usr/bin/env python3
import json,os,sys
if 'tar' in sys.argv: sys.stdin.buffer.read()
if '.network-enabled' in ' '.join(sys.argv): print('enabled')
with open(os.environ['KAKAO_TEST_CALLS'],'a') as out:
 out.write(json.dumps(sys.argv[1:])+'\\n')
''')
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'], KAKAO_TEST_CALLS=str(log))
    env.pop('KAKAO_NETWORK', None)
    result = subprocess.run(['bash','scripts/lima-compose.sh','config','--quiet'], cwd=ROOT, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'deploy/compose.network.yaml' in json.loads(log.read_text().splitlines()[-1])
    env['KAKAO_NETWORK'] = '0'
    result = subprocess.run(['bash','scripts/lima-compose.sh','config','--quiet'], cwd=ROOT, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'deploy/compose.network.yaml' not in json.loads(log.read_text().splitlines()[-1])

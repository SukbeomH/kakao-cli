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


def test_vm_vpn_mss_rules_are_bidirectional_idempotent_and_scoped(tmp_path):
    # No real firewall is touched. Emulate the kernel's existing-rule checks.
    log, state = tmp_path/'iptables.jsonl', tmp_path/'rules.json'
    stub(tmp_path/'uname', '#!/bin/sh\necho Linux\n')
    stub(tmp_path/'iptables', '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
log=Path(os.environ['MSS_TEST_LOG']); state=Path(os.environ['MSS_TEST_STATE'])
with log.open('a') as f:f.write(json.dumps(args)+'\\n')
rules=json.loads(state.read_text()) if state.exists() else []
i=next(i for i,a in enumerate(args) if a in ('-C','-A')); rule=args[i+1:]
if args[i]=='-C':sys.exit(0 if rule in rules else 1)
rules.append(rule);state.write_text(json.dumps(rules))
''')
    # EUID is readonly in bash; use a Linux root emulation by dropping only the
    # guard from a temp copy. The production script keeps its actual root check.
    source=(ROOT/'scripts/vm-vpn-mss.sh').read_text()
    assert 'Linux' in source and '"$EUID" == 0' in source
    copy=tmp_path/'mss.sh'
    copy.write_text('\n'.join(l for l in source.splitlines() if not l.startswith('[[ "$(uname'))+'\n')
    env=dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
             MSS_TEST_LOG=str(log), MSS_TEST_STATE=str(state))
    for _ in range(2):
        result=subprocess.run(['bash',str(copy)],env=env,capture_output=True,text=True,timeout=5)
        assert result.returncode==0,result.stderr
    rules=json.loads(state.read_text())
    assert len(rules)==2
    assert all(r[0]=='FORWARD' for r in rules)
    assert {r[1] for r in rules}=={'-i','-o'}
    for r in rules:
        assert r[2]=='tailscale0' and r[r.index('-p')+1]=='tcp'
        assert r[r.index('--tcp-flags')+1:r.index('--tcp-flags')+3]==['SYN,RST','SYN']
        assert r[-3:]==['-j','TCPMSS','--clamp-mss-to-pmtu']
    calls=[json.loads(l) for l in log.read_text().splitlines()]
    assert all(c[:4]==['--wait','5','-t','mangle'] for c in calls)
    assert all('-F' not in c and '-D' not in c for c in calls)


def test_vm_vpn_mss_runs_before_agent_and_restarts_with_tailscaled():
    unit=(ROOT/'deploy/kakao-vpn-mss.service').read_text()
    agent=(ROOT/'deploy/kakao-network-agent.service').read_text()
    setup=(ROOT/'scripts/setup-vm-vpn.sh').read_text()
    assert 'PartOf=tailscaled.service' in unit
    assert 'Before=kakao-network-agent.service docker.service' in unit
    assert 'kakao-vpn-mss.service' in agent.split('[Service]')[0]
    assert 'modprobe xt_TCPMSS' in setup
    assert 'systemctl restart kakao-vpn-mss.service' in setup

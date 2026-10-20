"""공식 SQLCipher Android를 기기 내부에서만 사용하는 일회성 프로필 리더.

키/DB 바이트를 운반하지 않는다. 이름 결과만 메모리로 받으며 실패는 ID fallback.
공식 AAR/so 및 자체 빌드 dex는 저장소 밖 캐시에만 두고 고정 해시로 확인한다.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import urllib.request
import zipfile

VERSION = '4.10.0'
AAR_URL = 'https://repo.maven.apache.org/maven2/net/zetetic/sqlcipher-android/4.10.0/sqlcipher-android-4.10.0.aar'
AAR_SHA256 = 'cc60b1a40d023bec06a1e56740db7172d2516668570140cd9de00ca90f84cd9f'
SQLITE_URL = 'https://dl.google.com/dl/android/maven2/androidx/sqlite/sqlite/2.2.0/sqlite-2.2.0.aar'
SQLITE_SHA256 = '6156d5d2c17bd8c5460f199142e4283053b1da750994f6b396c62c50fcc7270c'
SO_SHA256 = {
    'arm64-v8a': 'bc85746647ce4ea5f390eccd61e5236fbd11786a91802877b9fdbbb66a55192d',
    'armeabi-v7a': 'a3d6e16e4477961cffc563ad9e9b49aa9cf0f4def5f008a9b349c9afacec6170',
    'x86': 'efd77986abfa03cff177ea1d9801bd3080bd8bfc90a26ac1a977da169c5b89c8',
    'x86_64': '482f62c4d77e136d7cb7eb30d74a5c4cbadc3577d10ced1f13f3e745e1be7e7a',
}
HELPER_SHA256 = '165b5b7e69acbfe4245be9b8a0f6fbbac4ee0cc101fa2691924c15956d5f464a'
DEVICE_DIR = '/data/local/tmp/kakao-cli-profile-helper'
CACHE = Path('~/.cache/kakao-cli/sqlcipher-4.10.0').expanduser()
MAX_RESPONSE = 32 * 1024 * 1024


class ProfileUnavailable(RuntimeError):
    pass


def verified_bytes(path, expected):
    try:
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise ProfileUnavailable('프로필 구성요소 해시 검증 실패') from None


def artifact(url, name, expected, cache=CACHE):
    """공개 배포물만 저장. 다운로드/검증 실패의 원문을 로그에 넣지 않는다."""
    cache = Path(cache)
    dest = cache / name
    if dest.exists():
        verified_bytes(dest, expected)
        return dest
    cache.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix=name+'.', delete=False) as out:
            temporary = Path(out.name)
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError()
            out.write(data)
        os.replace(temporary, dest)
        return dest
    except Exception:
        raise ProfileUnavailable('공식 프로필 구성요소를 준비하지 못했습니다') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _integer(value):
    return type(value) is int and -(1 << 63) <= value < (1 << 63)


def parse_result(data):
    try:
        if len(data) > MAX_RESPONSE:
            raise ValueError()
        result = json.loads(data)
        if not isinstance(result, dict) or set(result) != {'ok', 'read_only', 'users', 'channels'}:
            raise ValueError()
        if result['ok'] is not True or result['read_only'] is not True:
            raise ValueError()
        users, channels, seen = {}, {}, set()
        for category in ('users', 'channels'):
            records = result[category]
            if not isinstance(records, list) or len(records) > 50000:
                raise ValueError()
            for row in records:
                fields = {'id', 'name'} if category == 'users' else {'id', 'chat_id', 'name'}
                if not isinstance(row, dict) or set(row) != fields or not _integer(row['id']):
                    raise ValueError()
                name = row['name']
                if not isinstance(name, str) or not name.strip() or len(name) > 4096:
                    raise ValueError()
                if category == 'users':
                    if row['id'] in users:
                        raise ValueError()
                    users[row['id']] = name
                else:
                    if not _integer(row['chat_id']) or (row['id'], row['chat_id']) in seen:
                        raise ValueError()
                    seen.add((row['id'], row['chat_id']))
                    channels.setdefault(row['chat_id'], []).append((row['id'], name))
        return users, channels
    except Exception:
        raise ProfileUnavailable('기기 내부 프로필 조회 실패: 기존 식별자를 유지합니다') from None


def _run(dev, command, timeout=45):
    try:
        return subprocess.run([dev.adb, '-s', dev.serial, 'exec-out', command],
                              capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except Exception:
        raise ProfileUnavailable('기기 내부 프로필 실행 실패') from None


def _read_profiles(dev):
    """동시 helper는 거절. 소유한 전용 임시 디렉터리만 finally에서 정리한다."""
    helper = CACHE / 'profile-helper-v1.jar'
    verified_bytes(helper, HELPER_SHA256)  # 빌드가 없으면 네트워크/기기 변경 없이 fallback.
    abi = _run(dev, 'getprop ro.product.cpu.abi').stdout.decode().strip()
    if abi not in SO_SHA256:
        raise ProfileUnavailable('지원하지 않는 프로필 엔진 ABI')
    aar = artifact(AAR_URL, 'sqlcipher-android-4.10.0.aar', AAR_SHA256)
    try:
        with zipfile.ZipFile(aar) as archive:
            library = archive.read('jni/' + abi + '/libsqlcipher.so')
        if hashlib.sha256(library).hexdigest() != SO_SHA256[abi]:
            raise ValueError()
    except Exception:
        raise ProfileUnavailable('공식 프로필 엔진 무결성 실패') from None
    # 공개 라이브러리만 호스트 캐시에 추출. DB/키/이름은 파일에 쓰지 않는다.
    lib = CACHE / ('libsqlcipher-' + abi + '.so')
    if lib.exists():
        verified_bytes(lib, SO_SHA256[abi])
    else:
        with tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as temp:
            temp.write(library)
            temporary = Path(temp.name)
        os.replace(temporary, lib)
    owned = False
    directory = shlex.quote(DEVICE_DIR)
    try:
        setup = _run(dev, 'umask 077; test "$(id -u)" = 0 && mkdir ' + directory + ' && printf prepared')
        if setup.stdout != b'prepared':
            raise ProfileUnavailable('프로필 helper가 사용 중이거나 준비되지 않았습니다')
        owned = True
        for local, name in ((helper, 'helper.jar'), (lib, 'libsqlcipher.so')):
            p = subprocess.run([dev.adb, '-s', dev.serial, 'push', str(local), DEVICE_DIR+'/'+name],
                               capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
            if p.returncode:
                raise ProfileUnavailable('프로필 helper 전송 실패')
        expected = HELPER_SHA256 + '  ' + DEVICE_DIR + '/helper.jar\n' + SO_SHA256[abi] + '  ' + DEVICE_DIR + '/libsqlcipher.so\n'
        check = _run(dev, 'printf %s ' + shlex.quote(expected) + ' | sha256sum -c >/dev/null && printf verified')
        if check.stdout != b'verified':
            raise ProfileUnavailable('기기 프로필 helper 해시 검증 실패')
        command = ('ulimit -c 0; mkdir -p '+directory+'/art; '
                   'CLASSPATH='+directory+'/helper.jar ANDROID_DATA='+directory+'/art '
                   'app_process /data/local/tmp kakao.cli.ProfileReader & task_pid=$!; '
                   'printf %s "$task_pid" > '+directory+'/pid; wait "$task_pid"')
        response = _run(dev, command)
        return parse_result(response.stdout)
    except ProfileUnavailable:
        raise
    except Exception:
        raise ProfileUnavailable('기기 내부 프로필 조회 실패') from None
    finally:
        if owned:
            # 시간 초과가 났어도 이 디렉터리에서 실행한 helper만 종료한다.
            cleanup = ('if test -f '+directory+'/pid; then task_pid=$(cat '+directory+'/pid); '
                       'case "$task_pid" in ""|*[!0-9]*) ;; *) '
                       'if test -r /proc/"$task_pid"/cmdline && '
                       'tr "\\000" " " < /proc/"$task_pid"/cmdline | grep -q kakao.cli.ProfileReader; '
                       'then kill "$task_pid" 2>/dev/null; fi;; esac; fi; rm -rf '+directory+' && test ! -e '+directory+' && printf cleaned')
            if _run(dev, cleanup, timeout=10).stdout != b'cleaned':
                raise ProfileUnavailable('프로필 helper 정리를 확인하지 못했습니다')


def read_profiles(dev):
    try:
        return _read_profiles(dev)
    except ProfileUnavailable:
        raise
    except Exception:
        raise ProfileUnavailable('기기 내부 프로필을 준비하지 못했습니다') from None

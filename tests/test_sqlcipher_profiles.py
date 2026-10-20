"""가짜 이름·키로 SQLCipher 경계 검증. 실기 자격증명/DB fixture 없음."""
import json
from types import SimpleNamespace
import pytest


def test_official_artifacts_are_fixed_and_nonfork():
    from kakao_cli import profiles
    assert profiles.VERSION == '4.10.0'
    assert profiles.AAR_SHA256 == 'cc60b1a40d023bec06a1e56740db7172d2516668570140cd9de00ca90f84cd9f'
    assert profiles.AAR_URL.startswith('https://repo.maven.apache.org/maven2/net/zetetic/')
    assert profiles.DEVICE_DIR.startswith('/data/local/tmp/')


def test_result_validation_rejects_malformed_and_duplicate_identity():
    from kakao_cli import profiles
    valid = {'ok': True, 'read_only': True, 'users': [{'id': 11, 'name': '가짜 별명'}],
             'channels': [{'id': 12, 'chat_id': 13, 'name': '가짜 채널'}]}
    users, channels = profiles.parse_result(json.dumps(valid).encode())
    assert users == {11: '가짜 별명'} and channels == {13: [(12, '가짜 채널')]}
    for changed in ({**valid, 'read_only': False}, {**valid, 'users': valid['users'] * 2},
                    {**valid, 'key': 'FAKE-KEY'}, {'ok': False, 'error': '가짜 비밀 오류'}):
        with pytest.raises(profiles.ProfileUnavailable) as error:
            profiles.parse_result(json.dumps(changed).encode())
        assert 'FAKE-KEY' not in str(error.value) and '가짜 비밀' not in str(error.value)


def test_checksum_mismatch_is_rejected_before_use(tmp_path):
    from kakao_cli import profiles
    artifact = tmp_path/'fake-artifact'
    artifact.write_bytes(b'fake damaged bytes')
    with pytest.raises(profiles.ProfileUnavailable):
        profiles.verified_bytes(artifact, '0'*64)


def test_profile_failure_keeps_readonly_id_fallback(monkeypatch):
    from kakao_cli import kakaodb as db, profiles
    obj = object.__new__(db.Database)
    obj.crypto_users, obj.crypto_channels = {}, {}
    obj.dev = object()
    def failure(dev): raise profiles.ProfileUnavailable('가짜 실패')
    monkeypatch.setattr(profiles, 'read_profiles', failure)
    obj._load_crypto_profiles()
    assert obj.crypto_users == {} and obj.crypto_channels == {}


def test_channel_requires_unique_member_or_sender_identity():
    from kakao_cli import kakaodb as db
    obj = object.__new__(db.Database)
    obj.self_id, obj.names, obj.open_links = 1, {}, {}
    obj.crypto_users = {2: '가짜 친구'}
    obj.crypto_channels = {3: [(4, '가짜 채널')]}
    obj._query = lambda *a: []
    room = {'id': 3, 'type': 'PlusChat', 'link_id': 0, 'private_meta': '{}', 'meta': '[]', 'members': '[4]'}
    assert obj.room_name(room) == '가짜 채널'
    room['members'] = '[5]'
    assert obj.room_name(room) == 'room:3'
    room['members'] = '[4]';obj.crypto_channels[3].append((5, '가짜 둘째 채널'))
    assert obj.room_name(room) == 'room:3'
    assert obj._sender(2, 0) == '가짜 친구'
    assert obj._sender(2, 99) == 'user:2'  # 오픈채팅 별명을 전역 연락처 이름으로 바꾸지 않는다.


@pytest.fixture(scope='module')
def java_classes(tmp_path_factory):
    from pathlib import Path
    import subprocess
    root = Path(__file__).resolve().parents[1]
    dest = tmp_path_factory.mktemp('profile-java')
    files = [root/'src/kakao_cli/resources/ProfileReader.java', *sorted((root/'tests/java').rglob('*.java'))]
    result = subprocess.run(['javac', '--release', '8', '-encoding', 'UTF-8', '-g:none', '-d', str(dest), *map(str, files)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return dest


@pytest.mark.parametrize('case', ['fallback', 'salt', 'invalid', 'names', 'logging', 'readonly', 'corruption', 'wrong_mode'])
def test_device_java_contracts_with_synthetic_data(java_classes, case):
    import hashlib
    import subprocess
    # 독립 Python PBKDF2로 생성한 가짜 salt=12345 벡터. 실키 아님.
    expected = hashlib.pbkdf2_hmac('sha256', bytes([4,15,81,123,77,5,23,99,2,111,10,31,54,29,109,97]),
                                 b'se12345ed'.ljust(16, b'\0'), 4096, 32).hex()
    result = subprocess.run(['java', '-cp', str(java_classes), 'kakao.cli.ProfileReaderTest', case, expected],
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'synthetic_ok'


@pytest.fixture
def fake_transport(tmp_path, monkeypatch):
    import hashlib
    import zipfile
    from kakao_cli import profiles
    helper = tmp_path/'profile-helper-v1.jar'; helper.write_bytes(b'FAKE-DEX')
    lib = b'FAKE-NATIVE'; aar = tmp_path/'fake.aar'
    with zipfile.ZipFile(aar, 'w') as z: z.writestr('jni/x86_64/libsqlcipher.so', lib)
    monkeypatch.setattr(profiles, 'CACHE', tmp_path)
    monkeypatch.setattr(profiles, 'HELPER_SHA256', hashlib.sha256(b'FAKE-DEX').hexdigest())
    monkeypatch.setattr(profiles, 'SO_SHA256', {'x86_64': hashlib.sha256(lib).hexdigest()})
    monkeypatch.setattr(profiles, 'artifact', lambda *a, **kw: aar)
    monkeypatch.setattr(profiles.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    state = {'commands': [], 'busy': False, 'bad_hash': False, 'timeout': False, 'invalid_json': False, 'cleanup_bad': False}
    def remote(dev, command, timeout=45):
        state['commands'].append(command)
        if command.startswith('getprop '): stdout = b'x86_64'
        elif 'printf prepared' in command: stdout = b'' if state['busy'] else b'prepared'
        elif 'sha256sum -c' in command: stdout = b'' if state['bad_hash'] else b'verified'
        elif 'app_process' in command:
            if state['timeout']: raise profiles.ProfileUnavailable('FAKE timeout')
            stdout = b'FAKE-PRIVATE-ERROR' if state['invalid_json'] else b'{"ok":true,"read_only":true,"users":[],"channels":[]}'
        else: stdout = b'' if state['cleanup_bad'] else b'cleaned'
        return SimpleNamespace(stdout=stdout, stderr=b'', returncode=0)
    monkeypatch.setattr(profiles, '_run', remote)
    return profiles, state, SimpleNamespace(adb='fake-adb', serial='fake')


def test_transport_never_carries_key_and_cleans_owned_helper(fake_transport):
    profiles, state, dev = fake_transport
    assert profiles.read_profiles(dev) == ({}, {})
    assert 'rm -rf ' + profiles.DEVICE_DIR in state['commands'][-1]
    assert any('app_process' in c for c in state['commands'])
    assert all('passphrase' not in c.lower() and 'PRAGMA key' not in c for c in state['commands'])


def test_busy_helper_is_not_deleted(fake_transport):
    profiles, state, dev = fake_transport
    state['busy'] = True
    with pytest.raises(profiles.ProfileUnavailable): profiles.read_profiles(dev)
    assert not any('rm -rf' in c or 'app_process' in c for c in state['commands'])


@pytest.mark.parametrize('failure', ['bad_hash', 'timeout', 'invalid_json'])
def test_failure_cleans_helper_and_never_uses_recovery(fake_transport, failure):
    profiles, state, dev = fake_transport
    state[failure] = True
    with pytest.raises(profiles.ProfileUnavailable) as error: profiles.read_profiles(dev)
    assert 'FAKE-PRIVATE-ERROR' not in str(error.value)
    assert 'rm -rf ' + profiles.DEVICE_DIR in state['commands'][-1]
    assert not any('rekey' in c or 'checkpoint' in c or 'VACUUM' in c for c in state['commands'])
    if failure == 'bad_hash': assert not any('app_process' in c for c in state['commands'])


def test_cleanup_must_be_confirmed_before_returning_names(fake_transport):
    profiles, state, dev = fake_transport
    state['cleanup_bad'] = True
    with pytest.raises(profiles.ProfileUnavailable): profiles.read_profiles(dev)


def test_cache_io_error_keeps_private_error_out(fake_transport, monkeypatch):
    profiles, state, dev = fake_transport
    def broken(*args, **kwargs): raise OSError('FAKE-PRIVATE-ERROR')
    monkeypatch.setattr(profiles, 'artifact', broken)
    with pytest.raises(profiles.ProfileUnavailable) as error: profiles.read_profiles(dev)
    assert 'FAKE-PRIVATE-ERROR' not in str(error.value)


def test_tampered_helper_never_reaches_device(fake_transport):
    profiles, state, dev = fake_transport
    (profiles.CACHE/'profile-helper-v1.jar').write_bytes(b'TAMPERED_FAKE_DEX')
    with pytest.raises(profiles.ProfileUnavailable): profiles.read_profiles(dev)
    assert state['commands'] == []


def test_download_hash_failure_leaves_no_cache_payload(tmp_path, monkeypatch):
    from kakao_cli import profiles
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, size): return b'FAKE_UNTRUSTED_DOWNLOAD'
    monkeypatch.setattr(profiles.urllib.request, 'urlopen', lambda *a, **kw: Response())
    with pytest.raises(profiles.ProfileUnavailable):
        profiles.artifact('https://example.invalid/test', 'fake.aar', '0'*64, cache=tmp_path)
    assert list(tmp_path.iterdir()) == []

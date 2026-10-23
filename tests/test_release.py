"""Release bootstrap boundaries without starting a VM or installing an APK."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("download_apks", ROOT / "scripts/download-apks.py")
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)
build_spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build-release.py")
builder = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(builder)


def executable(path, source):
    path.write_text(source)
    path.chmod(0o755)


@pytest.fixture
def payload(monkeypatch):
    apk = b"signed apk fixture"
    ime = b"keyboard fixture"
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("base.apk", apk)
        archive.writestr("../../outside", b"unselected entry")
    data = bundle.getvalue()
    sha = lambda content: hashlib.sha256(content).hexdigest()
    manifest = {
        "kakaotalk": {"url": "https://example.test/talk", "sha256": sha(data),
                      "apks": {"base.apk": sha(apk)}},
        "adbkeyboard": {"url": "https://example.test/ime", "sha256": sha(ime)},
    }

    def download(url, target, expected):
        target.write_bytes(data if url.endswith("talk") else ime)
        downloader.check(target, expected)

    monkeypatch.setattr(downloader, "download", download)
    return manifest


def test_download_extracts_only_verified_manifest_members(tmp_path, payload):
    destination = tmp_path / "apks"
    downloader.prepare(destination, payload)
    assert {p.name for p in destination.iterdir()} == {"base.apk", "ADBKeyboard.apk"}
    assert not (tmp_path / "outside").exists()
    downloader.prepare(destination, payload)  # Valid cache is reusable.


def test_download_wrong_split_hash_publishes_no_apks(tmp_path, payload):
    payload["kakaotalk"]["apks"]["base.apk"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        downloader.prepare(tmp_path, payload)
    assert not list(tmp_path.iterdir())


def test_download_never_overwrites_unexpected_or_symlinked_cache(tmp_path, payload):
    outside = tmp_path / "keep"
    outside.write_bytes(b"keep")
    destination = tmp_path / "apks"
    destination.mkdir()
    (destination / "base.apk").symlink_to(outside)
    with pytest.raises(ValueError):
        downloader.prepare(destination, payload)
    assert outside.read_bytes() == b"keep"


def test_duplicate_archive_member_is_rejected(tmp_path, payload, monkeypatch):
    def duplicate_download(url, target, expected):
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("base.apk", b"one")
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("base.apk", b"two")
    monkeypatch.setattr(downloader, "download", duplicate_download)
    with pytest.raises(ValueError, match="duplicate"):
        downloader.prepare(tmp_path, payload)


@pytest.mark.parametrize("bad_certificate", [False, True])
def test_installer_verifies_all_signatures_before_install(tmp_path, bad_certificate):
    log = tmp_path / "calls"
    executable(tmp_path / "adb", '''#!/bin/sh
printf 'adb %s\\n' "$*" >> "$TASK_TEST_LOG"
''')
    executable(tmp_path / "java", "#!/bin/sh\nexit 0\n")
    manifest = json.loads((ROOT / "deploy/apks.json").read_text())
    talk_cert = manifest["kakaotalk"]["certificate_sha256"]
    ime_cert = manifest["adbkeyboard"]["certificate_sha256"]
    executable(tmp_path / "apksigner", f'''#!/bin/sh
printf 'verify %s\\n' "$*" >> "$TASK_TEST_LOG"
case "$*" in *ADBKeyboard.apk*) cert={ime_cert} ;; *) cert={talk_cert} ;; esac
if [ "$TASK_BAD_CERT" = 1 ]; then cert=incorrect; fi
echo "Signer #1 certificate SHA-256 digest: $cert"
''')
    executable(tmp_path / "python3", f'''#!{sys.executable}
import os,sys
if sys.argv[1] == 'scripts/download-apks.py':
    sys.exit(0)
os.execv({sys.executable!r}, [{sys.executable!r}] + sys.argv[1:])
''')
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", TASK_TEST_LOG=str(log),
               TASK_BAD_CERT=str(int(bad_certificate)))
    result = subprocess.run(["bash", "scripts/install-android-apks.sh", str(tmp_path / "apks")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    calls = log.read_text().splitlines()
    installs = [i for i, line in enumerate(calls) if line.startswith("adb -s 127.0.0.1:5555 install")]
    if bad_certificate:
        assert result.returncode != 0
        assert not installs
    else:
        assert result.returncode == 0, result.stderr
        assert len(installs) == 2
        assert len([line for line in calls[:installs[0]] if line.startswith("verify ")]) == 5


def test_bootstrap_help_has_no_side_effects(tmp_path):
    executable(tmp_path / "limactl", "#!/bin/sh\nexit 99\n")
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    result = subprocess.run(["bash", "scripts/setup-macos.sh", "--help"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert "Usage:" in result.stdout


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_sequence_with_mock_vm(tmp_path, existing):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/setup-macos.sh", project / "scripts/setup-macos.sh")
    log = tmp_path / "calls"
    executable(project / "scripts/lima-compose.sh", '''#!/bin/sh
printf 'compose %s\\n' "$*" >> "$TASK_TEST_LOG"
''')
    executable(tmp_path / "uname", '''#!/bin/sh
case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac
''')
    executable(tmp_path / "limactl", '''#!/bin/sh
printf 'lima %s\\n' "$*" >> "$TASK_TEST_LOG"
case "$*" in
  'list --format '{{.Name}}) [ "$TASK_EXISTING" = 0 ] || echo kakao ;;
  *'getprop sys.boot_completed') echo 1 ;;
esac
exit 0
''')
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", TASK_TEST_LOG=str(log),
               TASK_EXISTING=str(int(existing)))
    result = subprocess.run(["bash", "scripts/setup-macos.sh"], cwd=project, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    start = next(line for line in calls if line.startswith('lima start'))
    assert ('--name=kakao' in start) is not existing
    assert calls.index('compose up -d redroid') < calls.index('compose run --rm boot')
    assert calls.index('compose run --rm boot') < calls.index('compose up -d webui')
    assert not any('delete' in line or 'down' in line or 'stop' in line for line in calls)


def test_gitless_release_wrapper_uses_manifest_and_excludes_sensitive_paths(tmp_path):
    project = tmp_path / "release"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/lima-compose.sh", project / "scripts/lima-compose.sh")
    (project / "Dockerfile").write_text("FROM scratch\n")
    (project / "release-files.txt").write_text("Dockerfile\nscripts/lima-compose.sh\n")
    log = tmp_path / "calls.jsonl"
    executable(tmp_path / "limactl", f'''#!{sys.executable}
import json,os,sys,tarfile
result={{'args':sys.argv[1:]}}
if 'tar' in sys.argv:
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
        result['files']=[member.name for member in archive]
with open(os.environ['TASK_TEST_LOG'],'a') as out:
    out.write(json.dumps(result)+'\\n')
''')
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", TASK_TEST_LOG=str(log))
    command = ["bash", str(project / "scripts/lima-compose.sh")]
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert next(c['files'] for c in calls if 'files' in c) == ['Dockerfile', 'scripts/lima-compose.sh']
    assert calls[-1]['args'][-2:] == ['config', '--quiet']
    (project / "release-files.txt").write_text("deploy/.env\n")
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0


@pytest.mark.parametrize("tracked_apk", [False, True])
def test_release_archive_excludes_local_state_and_refuses_tracked_binaries(tmp_path, tracked_apk):
    import tarfile
    project = tmp_path / "project"
    project.mkdir()
    sources = {
        'pyproject.toml': '[project]\nversion="0.1.0"\n',
        'scripts/setup-macos.sh': '#!/bin/sh\n',
        'scripts/download-apks.py': '# source\n',
        'scripts/benchmark-console.cjs': '// source\n',
        'scripts/kakao-binder.service': '[Unit]\n',
        'deploy/apks.json': '{}\n',
        'deploy/lima.yaml': 'cpus: 2\n',
        'packages/android-console/pyproject.toml': '[project]\nname="android-console"\n',
        'packages/android-console/src/android_console/webui.py': '# shared console\n',
    }
    for name, content in sources.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    subprocess.run(['git', 'add', '.'], cwd=project, check=True)
    (project / 'deploy/.env').write_text('personal secret')
    (project / 'deploy/private.key').write_text('personal key')
    (project / 'deploy/app.apk').write_bytes(b'private binary')
    if tracked_apk:
        subprocess.run(['git', 'add', 'deploy/app.apk'], cwd=project, check=True)
        with pytest.raises(ValueError, match='Non-source file'):
            builder.build(project, tmp_path / 'dist')
        assert not (tmp_path / 'dist').exists()
    else:
        archive = builder.build(project, tmp_path / 'dist')
        with tarfile.open(archive) as tar:
            names = tar.getnames()
            assert len(names) == len(sources) + 1
            assert any(name.endswith('/release-files.txt') for name in names)
            assert not any(name.endswith(('.env', '.key', '.apk')) or '/.git/' in name for name in names)

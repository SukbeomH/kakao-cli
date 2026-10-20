#!/usr/bin/env python3
"""공식 AAR + 자체 Java 소스를 캐시의 DEX jar로 빌드. 바이너리 커밋 금지."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from kakao_cli import profiles


def build(d8, print_hash=False):
    cache = profiles.CACHE
    vendor = profiles.artifact(profiles.AAR_URL, 'sqlcipher-android-4.10.0.aar', profiles.AAR_SHA256)
    sqlite = profiles.artifact(profiles.SQLITE_URL, 'sqlite-2.2.0.aar', profiles.SQLITE_SHA256)
    source = Path(__file__).resolve().parents[1]/'src/kakao_cli/resources/ProfileReader.java'
    with tempfile.TemporaryDirectory(prefix='build-profile-', dir=cache) as temporary:
        root = Path(temporary)
        classes, dex = root/'classes', root/'dex'
        classes.mkdir(); dex.mkdir()
        dependencies = []
        for name, archive in [('sqlcipher', vendor), ('sqlite', sqlite)]:
            dest = root/(name+'.jar')
            with zipfile.ZipFile(archive) as aar:
                dest.write_bytes(aar.read('classes.jar'))
            dependencies.append(dest)
        subprocess.run(['javac', '--release', '8', '-Xlint:-options', '-encoding', 'UTF-8', '-g:none', '-d', str(classes), str(source)], check=True)
        own = root/'own.jar'
        with zipfile.ZipFile(own, 'w') as jar:
            for path in sorted(classes.rglob('*.class')):
                entry=zipfile.ZipInfo(path.relative_to(classes).as_posix(), (1980,1,1,0,0,0))
                jar.writestr(entry,path.read_bytes())
        subprocess.run([d8, '--release', '--min-api', '26', '--output', str(dex), str(own), *map(str,dependencies)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        output = root/'profile-helper-v1.jar'
        with zipfile.ZipFile(output,'w') as jar:
            for path in sorted(dex.glob('*.dex')):
                entry=zipfile.ZipInfo(path.name,(1980,1,1,0,0,0))
                jar.writestr(entry,path.read_bytes())
            for name in ('SQLCIPHER-LICENSE.txt','ANDROIDX-LICENSE.txt'):
                entry=zipfile.ZipInfo('META-INF/'+name,(1980,1,1,0,0,0))
                jar.writestr(entry,(source.parent/name).read_bytes())
        digest=hashlib.sha256(output.read_bytes()).hexdigest()
        if print_hash:
            print(digest)
            return
        if digest != profiles.HELPER_SHA256:
            raise RuntimeError('helper 빌드 해시 불일치: 고정 도구/소스 확인 필요')
        os.replace(output,cache/'profile-helper-v1.jar')
        print('프로필 helper 빌드·해시 검증 완료')


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--d8',default=os.environ.get('KAKAO_D8','d8'))
    ap.add_argument('--print-hash',action='store_true',help='개발 중 재현 빌드의 공개 해시만 확인 (설치 없음)')
    args=ap.parse_args()
    build(args.d8,args.print_hash)

#!/usr/bin/env python3
"""Build our MIT input helper with the Android SDK; signing key stays in cache."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', default=os.environ.get('ANDROID_HOME', '/opt/homebrew/share/android-commandlinetools'))
    parser.add_argument('--build-tools', default='36.1.0')
    parser.add_argument('--platform', default='android-36')
    parser.add_argument('--verify', action='store_true', help='check APK dex matches this source; do not re-sign')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sdk = Path(args.sdk)
    tools, android = sdk/'build-tools'/args.build_tools, sdk/'platforms'/args.platform/'android.jar'
    cache = Path.home()/'.cache/android-console/build'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = cache/'ime.jks'
    def run(*cmd):
        subprocess.run(list(map(str, cmd)), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not args.verify and not key.exists():
        run('keytool', '-genkeypair', '-keystore', key, '-storepass', 'android', '-keypass', 'android', '-alias', 'console', '-dname', 'CN=Android Console', '-keyalg', 'RSA', '-validity', '10000')
        key.chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='console-ime-') as folder:
        temp = Path(folder)
        classes, dex = temp/'classes', temp/'dex'
        classes.mkdir(); dex.mkdir()
        run('javac', '--release', '8', '-encoding', 'UTF-8', '-g:none', '-classpath', android, '-d', classes, root/'ConsoleIME.java')
        run(tools/'d8', '--release', '--min-api', '26', '--lib', android, '--output', dex, *sorted(classes.rglob('*.class')))
        if args.verify:
            output = root/'android-console-ime.apk'
            with zipfile.ZipFile(output) as apk:
                if apk.read('classes.dex') != (dex/'classes.dex').read_bytes():
                    raise RuntimeError('Bundled APK dex differs from current Java source')
            if hashlib.sha256(output.read_bytes()).hexdigest() != (root/'android-console-ime.sha256').read_text().strip():
                raise RuntimeError('Bundled APK hash mismatch')
            run(tools/'apksigner', 'verify', output)
            print('Bundled APK signature, SHA-256 and Java source/dex match')
            return
        unsigned, aligned = temp/'unsigned.apk', temp/'aligned.apk'
        run(tools/'aapt', 'package', '-f', '-M', root/'AndroidManifest.xml', '-S', root/'res', '-I', android, '-F', unsigned)
        with zipfile.ZipFile(unsigned, 'a') as apk:
            apk.write(dex/'classes.dex', 'classes.dex')
        run(tools/'zipalign', '-f', '4', unsigned, aligned)
        output = root/'android-console-ime.apk'
        run(tools/'apksigner', 'sign', '--v4-signing-enabled', 'false', '--ks', key, '--ks-pass', 'pass:android', '--key-pass', 'pass:android', '--ks-key-alias', 'console', '--out', output, aligned)
        run(tools/'apksigner', 'verify', output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        (root/'android-console-ime.sha256').write_text(digest+'\n')
        print('Built Android Console IME: '+digest)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Download only pinned APK payloads; never start a VM or install an application."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

MANIFEST = Path(__file__).resolve().parents[1] / "deploy" / "apks.json"
MAX_DOWNLOAD = 512 * 1024 * 1024


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def check(path, expected):
    if path.is_symlink() or not path.is_file() or digest(path) != expected:
        raise ValueError(f"SHA-256 mismatch or invalid file: {path.name}")


def download(url, target, expected):
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS artifact URLs are accepted")
    request = urllib.request.Request(url, headers={"User-Agent": "kakao-cli-bootstrap/0.1.0"})
    print(f"Downloading {target.name}", flush=True)
    with urllib.request.urlopen(request, timeout=60) as response, target.open("xb") as out:
        if not response.geturl().startswith("https://"):
            raise ValueError("Refusing a non-HTTPS redirect")
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise ValueError("Artifact exceeds the download size limit")
            out.write(chunk)
    check(target, expected)


def prepare(destination, manifest):
    destination.mkdir(parents=True, exist_ok=True)
    talk = manifest["kakaotalk"]
    expected = dict(talk["apks"], **{"ADBKeyboard.apk": manifest["adbkeyboard"]["sha256"]})
    for name in expected:
        if Path(name).name != name or not name.endswith(".apk"):
            raise ValueError("Invalid APK filename in manifest")
        target = destination / name
        if target.exists() or target.is_symlink():
            check(target, expected[name])
    if all((destination / name).is_file() for name in expected):
        print(f"Verified cached APKs: {destination}")
        return
    # Stage on the same filesystem, verify every split before publishing any output.
    with tempfile.TemporaryDirectory(prefix=".download-", dir=destination) as staging:
        stage = Path(staging)
        bundle = stage / "kakaotalk.xapk"
        download(talk["url"], bundle, talk["sha256"])
        with zipfile.ZipFile(bundle) as archive:
            for name, sha in talk["apks"].items():
                entries = [info for info in archive.infolist() if info.filename == name]
                if len(entries) != 1 or entries[0].file_size > MAX_DOWNLOAD:
                    raise ValueError(f"Missing, duplicate or oversized APK: {name}")
                with archive.open(entries[0]) as source, (stage / name).open("xb") as out:
                    shutil.copyfileobj(source, out)
                check(stage / name, sha)
        ime = manifest["adbkeyboard"]
        download(ime["url"], stage / "ADBKeyboard.apk", ime["sha256"])
        for name in expected:
            # Existing files were verified above; never silently replace unexpected files.
            target = destination / name
            if target.exists() or target.is_symlink():
                check(target, expected[name])
            else:
                os.link(stage / name, target)
    print(f"Verified APKs ready: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        prepare(args.destination, json.loads(MANIFEST.read_text()))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"APK preparation failed: {error}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a source-only Mac bootstrap asset. APKs, VM disks, images and keys are excluded."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {"Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "README.md", "NOTICE"}
SOURCE_DIRS = {"src", "scripts", "deploy", "docs", "completions", "tests"}
SOURCE_SUFFIXES = {".py", ".sh", ".yaml", ".json", ".md", ".txt", ".java", ".js", ".1", ".service"}


def source_files(root):
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    selected = []
    for name in paths:
        if not name:
            continue
        path = Path(name)
        if name not in TOP_FILES and path.parts[0] not in SOURCE_DIRS:
            continue
        if any(part.startswith(".env") for part in path.parts):
            raise ValueError(f"Secret filename cannot be released: {name}")
        if name not in TOP_FILES and path.suffix not in SOURCE_SUFFIXES and path.parts[0] != "completions":
            raise ValueError(f"Non-source file cannot be released: {name}")
        full = root / name
        if full.is_symlink() or not full.is_file():
            raise ValueError(f"Release inputs must be regular files: {name}")
        selected.append(name)
    required = {"scripts/setup-macos.sh", "scripts/download-apks.py", "deploy/apks.json", "deploy/lima.yaml"}
    if not required.issubset(selected):
        raise ValueError("Release scripts must be tracked by Git before packaging")
    return sorted(selected)


def build(root, output):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    prefix = f"kakao-cli-{version}-macos-arm64"
    files = source_files(root)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{prefix}.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for name in files:
            info = tar.gettarinfo(str(root / name), arcname=f"{prefix}/{name}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.pax_headers = {}
            with (root / name).open("rb") as stream:
                tar.addfile(info, stream)
        manifest = ("\n".join(files) + "\n").encode()
        info = tarfile.TarInfo(f"{prefix}/release-files.txt")
        info.size = len(manifest)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(manifest))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{sha}  {archive.name}\n")
    print(json.dumps({"archive": str(archive), "sha256": sha, "files": len(files)}))
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(ROOT, parser.parse_args().output)

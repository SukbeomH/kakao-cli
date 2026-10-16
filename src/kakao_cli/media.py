"""File receive: pull KakaoTalk media blobs from external storage.

KakaoTalk stores sent/received media as content-addressed blobs (filename = sha256,
no extension) under /sdcard/Android/data/com.kakao.talk/contents/<cat>/<chatId>/<hh>/<hash>.
Readable via adb (ext_data_rw). We pull new blobs and detect type by magic bytes.
"""
from __future__ import annotations
import os

from .adb import Device

CONTENTS = "/sdcard/Android/data/com.kakao.talk/contents"

_MAGIC = [
    (b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"), (b"GIF89a", "gif"),
]


def _sniff(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(16)
    for sig, ext in _MAGIC:
        if head.startswith(sig):
            return ext
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return "bin"


def list_blobs(dev: Device) -> list[dict]:
    out = dev.shell(f"find {CONTENTS} -type f 2>/dev/null")
    items = []
    for p in out.splitlines():
        p = p.strip()
        if not p:
            continue
        parts = p.split("/")
        chat_id = parts[-3] if len(parts) >= 3 else ""
        items.append({"path": p, "chat_id": chat_id, "hash": parts[-1]})
    return items


def pull_new(dev: Device, dest: str, seen: set[str]) -> list[dict]:
    os.makedirs(dest, exist_ok=True)
    pulled = []
    for b in list_blobs(dev):
        if b["hash"] in seen:
            continue
        seen.add(b["hash"])
        tmp = os.path.join(dest, b["hash"])
        import subprocess
        r = subprocess.run([dev.adb, "-s", dev.serial, "pull", b["path"], tmp],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(tmp):
            continue
        ext = _sniff(tmp)
        final = tmp + "." + ext
        os.replace(tmp, final)
        pulled.append({"chat_id": b["chat_id"], "hash": b["hash"],
                       "type": ext, "path": final, "size": os.path.getsize(final)})
    return pulled

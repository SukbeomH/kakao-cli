#!/usr/bin/env bash
# Ubuntu/Linux 호스트 준비. compose 밖에서, 승인한 신규 호스트/전환 때만 실행.
# binderfs 방식 참고: kakaotalk-bridge(MIT), NOTICE 참조.
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'root 권한이 필요합니다' >&2; exit 1; }
[[ "$(getconf PAGESIZE)" == 4096 ]] || { echo 'redroid는 4K 페이지 커널이 필요합니다' >&2; exit 1; }
modprobe binder_linux devices=binder,hwbinder,vndbinder
mkdir -p /dev/binderfs
mountpoint -q /dev/binderfs || mount -t binder binder /dev/binderfs
# mount 직후 binder-control만 있는 커널에서는 BINDER_CTL_ADD로 노드를 할당한다.
# Linux UAPI include/uapi/linux/android/binderfs.h: char name[256], u32 major/minor.
python3 - <<'PYTHON'
import fcntl
import os
import stat
import struct

root = '/dev/binderfs'
size = struct.calcsize('256sII')
request = (3 << 30) | (size << 16) | (ord('b') << 8) | 1
for name in ('binder', 'hwbinder', 'vndbinder'):
    path = root + '/' + name
    if not os.path.exists(path):
        with open(root + '/binder-control', 'rb', buffering=0) as control:
            fcntl.ioctl(control.fileno(), request, bytearray(struct.pack('256sII', name.encode(), 0, 0)), True)
    if not stat.S_ISCHR(os.stat(path).st_mode):
        raise RuntimeError('binderfs 문자 장치가 아닙니다')
    os.chmod(path, 0o660)
PYTHON

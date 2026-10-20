#!/usr/bin/env bash
# 파일 설치·enable까지만. Docker/라이브 redroid를 시작·재시작하지 않는다.
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'root 권한이 필요합니다' >&2; exit 1; }
TASK_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install -m 0755 "$TASK_SCRIPT_DIR/prepare-binder.sh" /usr/local/sbin/kakao-prepare-binder
install -m 0644 "$TASK_SCRIPT_DIR/kakao-binder.service" /etc/systemd/system/kakao-binder.service
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/kakao-binder.conf <<'UNIT'
[Unit]
Requires=kakao-binder.service
After=kakao-binder.service
UNIT
systemctl daemon-reload
systemctl enable kakao-binder.service
printf '%s\n' '설치 완료. 승인한 유지보수 시점에 systemctl start kakao-binder.service를 실행하세요.'

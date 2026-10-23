#!/usr/bin/env bash
# Explicit isolated runtime transfer. No host-home mount or SSH agent forwarding.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_VM="${ANDROID_CONSOLE_VM:-android-console}"
[[ "$TASK_VM" != kakao ]] || { echo '기존 kakao VM 대신 별도 VM 이름을 사용하세요.' >&2; exit 2; }
[[ $# -gt 0 ]] || set -- config --quiet
for arg in "$@"; do
  case "$arg" in -v|--volumes|--volumes=*|--renew-anon-volumes) echo '데이터 볼륨 삭제 옵션은 지원하지 않습니다.' >&2; exit 2;; esac
done
limactl shell --workdir / "$TASK_VM" sudo mkdir -p /opt/android-console/runtime
# Only exported source/config files are transferred. No user file/data directory.
COPYFILE_DISABLE=1 tar --no-xattrs -C "$TASK_ROOT" -cf - \
  Dockerfile .dockerignore compose.yaml compose.lima.yaml keyboard.json install-keyboard.py python-package \
  | limactl shell --workdir / "$TASK_VM" sudo tar -xf - -C /opt/android-console/runtime
exec limactl shell --workdir /opt/android-console/runtime "$TASK_VM" \
  sudo env COMPOSE_DISABLE_ENV_FILE=1 \
  "ANDROID_CONSOLE_BIND=${ANDROID_CONSOLE_BIND:-127.0.0.1}" \
  "ANDROID_CONSOLE_FPS=${ANDROID_CONSOLE_FPS:-60}" \
  docker compose --project-name android-console -f compose.yaml -f compose.lima.yaml "$@"

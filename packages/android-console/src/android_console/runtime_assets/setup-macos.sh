#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_VM="${ANDROID_CONSOLE_VM:-android-console}"
if [[ "${1:-}" == --help ]]; then
  echo 'Usage: ./setup-macos.sh [--skip-keyboard]'
  echo 'Apple Silicon, Lima, CPU 2 / RAM 4GiB / disk 20GiB. Opens localhost:8801.'
  echo 'Downloads pinned Ubuntu/redroid images and optionally ADBKeyBoard; no application APK.'
  exit 0
fi
[[ $# == 0 || ( $# == 1 && "$1" == --skip-keyboard ) ]] || exit 2
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo 'Apple Silicon Mac 전용입니다.' >&2; exit 1; }
[[ "$TASK_VM" != kakao ]] || { echo '별도 VM 이름을 사용하세요.' >&2; exit 2; }
command -v limactl >/dev/null || { echo '먼저 brew install lima 를 실행하세요.' >&2; exit 1; }
cd "$TASK_ROOT"
limactl validate lima.yaml
if limactl list --format '{{.Name}}' | grep -Fxq "$TASK_VM"; then
  limactl start --tty=false "$TASK_VM"
else
  limactl start --name="$TASK_VM" --tty=false lima.yaml
fi
./lima-compose.sh config --quiet
./lima-compose.sh up -d redroid
TASK_READY=false
for ((attempt=0; attempt<120; attempt++)); do
  limactl shell --workdir / "$TASK_VM" adb connect 127.0.0.1:5555 >/dev/null 2>&1 || true
  TASK_BOOT="$(limactl shell --workdir / "$TASK_VM" adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  if [[ "$TASK_BOOT" == 1 ]]; then TASK_READY=true; break; fi
  sleep 5
done
[[ "$TASK_READY" == true ]] || { echo 'Android boot timed out.' >&2; exit 1; }
if [[ "${1:-}" != --skip-keyboard ]]; then
  limactl shell --workdir /opt/android-console/runtime "$TASK_VM" sudo python3 install-keyboard.py
fi
./lima-compose.sh up -d --build webui
echo '준비 완료: http://127.0.0.1:8801 (mirror)'
echo "종료: limactl stop $TASK_VM"

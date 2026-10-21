#!/usr/bin/env bash
# Explicit Apple Silicon bootstrap. Never logs in or sends a message.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_INSTANCE="${KAKAO_LIMA_INSTANCE:-kakao}"
if [[ "${1:-}" == --help ]]; then
  echo 'Usage: scripts/setup-macos.sh'
  echo 'Apple Silicon + Lima. Creates/starts the VM, downloads verified APKs/images and opens port 8800.'
  echo 'Existing installed apps and Android data are retained. Login is manual.'
  exit 0
fi
[[ $# == 0 ]] || { echo '지원하지 않는 인자입니다(--help 참고).' >&2; exit 2; }
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] \
  || { echo 'Apple Silicon Mac 전용 설치기입니다.' >&2; exit 1; }
command -v limactl >/dev/null || { echo '먼저 brew install lima 를 실행하세요.' >&2; exit 1; }
cd "$TASK_ROOT"
limactl validate deploy/lima.yaml
if limactl list --format '{{.Name}}' | grep -Fxq "$TASK_INSTANCE"; then
  limactl start --tty=false "$TASK_INSTANCE"
else
  limactl start --name="$TASK_INSTANCE" --tty=false deploy/lima.yaml
fi
scripts/lima-compose.sh config --quiet
scripts/lima-compose.sh up -d redroid
TASK_READY=false
for ((attempt=0; attempt<120; attempt++)); do
  limactl shell --workdir / "$TASK_INSTANCE" adb connect 127.0.0.1:5555 >/dev/null 2>&1 || true
  TASK_BOOT="$(limactl shell --workdir / "$TASK_INSTANCE" adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  if [[ "$TASK_BOOT" == 1 ]]; then TASK_READY=true; break; fi
  sleep 5
done
[[ "$TASK_READY" == true ]] || { echo 'Android 부팅 시간 초과. docs/deploy-compose.md의 문제 해결 절차를 확인하세요.' >&2; exit 1; }
limactl shell --workdir /opt/kakao-cli "$TASK_INSTANCE" sudo bash scripts/install-android-apks.sh
scripts/lima-compose.sh build webui
# Run boot explicitly even when a previous successful one-shot container exists.
scripts/lima-compose.sh run --rm boot
scripts/lima-compose.sh up -d webui
scripts/lima-compose.sh ps -a
echo '준비 완료: http://127.0.0.1:8800 에서 직접 QR 로그인하세요.'
echo "종료: limactl stop $TASK_INSTANCE"

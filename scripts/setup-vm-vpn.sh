#!/usr/bin/env bash
# Provision VM-only control after installing Tailscale in the guest.
set -euo pipefail
if [[ "${1:-}" != --guest ]]; then
  TASK_VM="${1:-kakao}"
  [[ "$TASK_VM" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo '잘못된 VM 이름' >&2; exit 2; }
  TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
  KAKAO_LIMA_INSTANCE="$TASK_VM" "$TASK_ROOT/scripts/lima-compose.sh" config --quiet
  exec limactl shell --workdir /opt/kakao-cli "$TASK_VM" sudo bash scripts/setup-vm-vpn.sh --guest
fi
[[ "$(uname -s)" == Linux && "$EUID" == 0 ]] || { echo 'Linux VM root에서 실행해야 합니다' >&2; exit 2; }
command -v tailscale >/dev/null || { echo '먼저 VM에 공식 Tailscale 패키지를 설치하세요' >&2; exit 2; }
# Current Tailscale uses CONNMARK; Lima's minimal kernel does not autoload it.
modprobe xt_connmark
modprobe xt_mark
printf '%s\n' xt_connmark xt_mark > /etc/modules-load.d/kakao-vpn.conf
install -m 0644 deploy/kakao-network-agent.service /etc/systemd/system/kakao-network-agent.service
systemctl daemon-reload
systemctl enable --now kakao-network-agent.service
systemctl restart kakao-network-agent.service
[[ -S /run/kakao-network/control.sock ]] || { sleep 1; test -S /run/kakao-network/control.sock; }
printf '%s\n' enabled > /opt/kakao-cli/.network-enabled
echo 'VM VPN 관리 준비 완료. webui를 재빌드한 뒤 CLI/TUI/웹에서 인증할 수 있습니다.'

#!/usr/bin/env bash
# Keep forwarded Android TCP packets within the selected VPN route's path MTU.
set -euo pipefail
[[ "$(uname -s)" == Linux && "$EUID" == 0 ]] || { echo 'Linux VM root에서 실행해야 합니다' >&2; exit 2; }
for direction in -o -i; do
  rule=(FORWARD "$direction" tailscale0 -p tcp --tcp-flags SYN,RST SYN
        -m comment --comment kakao-vpn-mss -j TCPMSS --clamp-mss-to-pmtu)
  # Rules affect only forwarded SYN/SYN-ACK packets crossing the VPN interface.
  # --wait shares the normal xtables lock with Docker and tailscaled.
  iptables --wait 5 -t mangle -C "${rule[@]}" 2>/dev/null \
    || iptables --wait 5 -t mangle -A "${rule[@]}"
done

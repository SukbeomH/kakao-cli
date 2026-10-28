# Lima VM 전용 Tailscale 관리

카카오 Lima VM의 인터넷 트래픽을 기존 Tailscale Exit node로 보냅니다.
맥의 Tailscale 설정·기본 경로·DNS는 변경하지 않습니다. Android에는 VPN 앱을 설치하지 않습니다.
VM 안의 Docker/Android 트래픽도 우회 대상이므로 VM 호스트와 Android 경로를 각각 확인해야 합니다.
Exit node는 이미 tailnet에서 승인된 장치여야 합니다.

## 최초 준비

`kakao` VM을 실행하고 **게스트에만** 공식 Linux Tailscale 패키지를 설치합니다.
[Tailscale Linux 설치 문서](https://tailscale.com/docs/install/linux)의 Ubuntu 방법을 사용하세요.
설치 스크립트를 맥에서 그대로 실행하면 안 됩니다. 예를 들어 게스트 셸로 들어간 후 설치합니다.

```sh
limactl start kakao
limactl shell --workdir / kakao
# 이 게스트 셸에서 공식 Ubuntu 설치 방법으로 tailscale 설치
# 설치 후 게스트 셸을 종료하고 아래는 저장소의 맥 터미널에서 실행
```

```sh
./scripts/setup-vm-vpn.sh kakao
./scripts/lima-compose.sh up -d --no-deps --build webui
```

준비 스크립트는 전용 Linux VM의 Tailscale 설치를 확인하고, 필요한 `xt_connmark`/`xt_mark`
모듈과 제한된 제어 서비스를 설정합니다. `.network-enabled` 표식을 통해 다음 업데이트 때도
선택적 Compose 구성을 유지합니다. Tailscale 패키지와 로그인 상태는 컨테이너 밖의 VM에 남습니다.
VM을 다시 만들면 새 장치로 인증해야 합니다.

회사 네트워크의 인증서 때문에 Tailscale이 `unknown authority`로 실패한다면 조직이 제공한
공개 CA 번들만 사용합니다. `/etc/tailscale/ca-bundle.pem`에 배치하고
`/etc/systemd/system/tailscaled.service.d/ca.conf`를 다음처럼 설정할 수 있습니다.
인증서·키를 저장소나 릴리즈에 넣지 않습니다.

```ini
[Service]
Environment=SSL_CERT_FILE=/etc/tailscale/ca-bundle.pem
```

```sh
# 게스트에서만 실행
sudo systemctl daemon-reload
sudo systemctl restart tailscaled kakao-network-agent
```

이는 tailscaled 프로세스에만 적용됩니다. TLS 검증을 끄거나 Android/맥의 신뢰 저장소를 바꾸지 않습니다.

## CLI

```sh
kakao vpn status
kakao vpn login --open         # 인증 URL을 맥 브라우저에서 열기
kakao vpn status --json
kakao vpn exit 100.64.0.1      # status에 표시된 온라인 Exit node의 IP
kakao vpn direct              # VM의 Exit node 우회 해제
```

로그인 URL에서 기존 Exit node가 속한 Tailscale 계정으로 **VM 장치 가입을 승인**합니다.
맥의 로그인 상태를 복제하지 않습니다. 인증 후 CLI/TUI/웹 UI 모두 같은 VM 상태를 표시합니다.
인증키를 명령줄에 입력할 필요가 없습니다. `direct`는 로그아웃이 아니므로 재인증 없이 다시 우회할 수 있습니다.

공통 `android-console` 패키지도 같은 기능을 제공합니다. 기본 콘솔 주소는 8801이고,
카카오 VM을 관리하려면 8800을 지정합니다.

```sh
android-console vpn status --url http://127.0.0.1:8800
android-console vpn login --url http://127.0.0.1:8800 --open
android-console vpn tui --url http://127.0.0.1:8800
# 원격 콘솔도 동일. 먼저 SSH 터널로 포트를 연결하는 방법을 권장합니다.
kakao vpn status --url http://127.0.0.1:18800
```

CLI/TUI는 이 HTTP 콘솔에 요청하며 로컬 macOS의 `tailscale` 명령을 실행하지 않습니다.
일반 `android-console` 서버는 `--network-socket` 또는 `ANDROID_CONSOLE_NETWORK_SOCKET`을
명시적으로 설정한 Linux VM에서만 VPN 관리가 활성화됩니다. 기본값은 비활성화입니다.

## TUI

`kakao vpn tui`로 VPN 전용 화면을 열 수 있습니다.
기존 `kakao tui`에서는 방 목록에 포커스가 있을 때 `v`를 누릅니다.
기기 연결이나 DB 준비 전에도 사용할 수 있는 VPN 전용 화면이 초기 로그인에 적합합니다.
기존 TUI는 `--vpn-url`로 관리 콘솔 주소를 지정할 수 있습니다.

| 키 | 동작 |
|---|---|
| `L` | VM 로그인 시작/대기 중 인증 URL 확인 |
| `O` | 인증 URL을 현재 터미널이 실행되는 환경의 브라우저로 열기 |
| 위/아래, Enter | 온라인 Exit node 선택·적용 |
| `D` | VM 우회 해제 |
| `R` | 상태 갱신 |
| Esc, `Q` | 화면 닫기 |

요청은 별도 스레드에서 처리하며 상태를 자동 갱신합니다.
SSH 안에서는 `O`가 로컬 맥 브라우저를 열 수 없으므로 표시된 URL을 복사해 맥에서 엽니다.

## 웹 UI

상단 **VM VPN** → **로그인 시작** → **브라우저에서 Tailscale 인증하기**를 사용합니다.
승인 후 온라인 Exit node를 선택하고 **선택한 경로 적용**을 누릅니다.
**우회 해제**는 VM의 직접 연결로 되돌립니다. 패널이 열려 있는 동안 상태를 자동 갱신합니다.

서버에는 임의 명령·인증키 입력·맥 네트워크 제어 API가 없습니다.
Linux VM의 전용 Unix 소켓으로 `status/login/exit/direct`만 처리합니다.
원본 tailscaled 관리 소켓이나 Docker 소켓을 웹 컨테이너에 노출하지 않습니다.
상태 응답과 인증 URL은 캐시하지 않으며 패널을 닫으면 URL·장치 목록을 지웁니다.

기존 웹 콘솔의 Host/동일 출처/JSON 검사를 적용합니다. 콘솔 자체에는 계정 인증이 없으므로
loopback 또는 인증된 SSH 터널 안에서 사용하고 인터넷에 직접 공개하지 않습니다.

## 확인 및 복구

```sh
kakao vpn status --json
limactl shell --workdir / kakao sudo tailscale status
limactl shell --workdir / kakao sudo systemctl status kakao-network-agent
limactl shell --workdir / kakao sudo journalctl -u tailscaled -u kakao-network-agent -n 40
```

인증 URL은 로그/이슈/스크린샷에 공유하지 마세요. 서비스는 URL을 자체 접근 로그에 남기지 않지만
Tailscale 자체 로그에 인증 안내가 있을 수 있습니다.

`Running`은 tailnet 로그인 상태입니다. 실제 카카오 연결 성공을 의미하지는 않습니다.
Exit node의 온라인 상태, VM과 Android의 외부 경로, 앱의 네트워크 오류 해소를 별도로 확인합니다.
`Some peers are advertising routes but --accept-routes is false`는 다른 장치의 서브넷 경로를
자동 수락하지 않는다는 안내입니다. Exit node 선택과는 별개입니다.

웹 콘솔 접근이 끊겨도 게스트 셸에서 VM의 우회를 해제할 수 있습니다.

```sh
limactl shell --workdir / kakao sudo tailscale set \
  --exit-node= --exit-node-allow-lan-access=false --accept-dns=false
```

관리 서비스 노출을 끄려면 `KAKAO_NETWORK=0 ./scripts/lima-compose.sh up -d --no-deps webui`를
사용하고, 지속 해제하려면 게스트의 `/opt/kakao-cli/.network-enabled` 표식을 제거하세요.
이는 Tailscale 자체의 우회 상태를 해제하지 않으므로 먼저 `kakao vpn direct`를 실행합니다.

공식 참고: [로그인 명령](https://tailscale.com/docs/reference/tailscale-cli/up),
[Exit node와 LAN 접근](https://tailscale.com/docs/features/exit-nodes).

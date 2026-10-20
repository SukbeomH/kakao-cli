# kakao-cli

공식 Android 카카오톡을 **redroid(Docker) / Waydroid**에서 구동하고 **읽기 전용 DB 수신 + adb UI 송신**으로
제어하는 CLI. 서버 프로토콜(LOCO)은 재구현하지 않고 공식 앱의 동기화 데이터를 읽는다.

- 텍스트·이미지·영상·문서 **송신**, 방별 **수신 폴링**, 미디어 **수집**
- **태블릿(서브 기기)**으로 로그인해 기존 폰과 **공존**
- Proxmox VM에서 **재부팅 자동복구**(systemd), 로그인 영속

> 비공식 자동화는 카카오 정책 위반 소지가 있다. 본인 계정, 소규모, 사람 유사 속도로만 사용하라.

## 목차
- [동작 방식](#동작-방식)
- [요구 사항](#요구-사항)
- [환경 구축](#환경-구축)
- [설치 / 실행](#설치--실행)
- [명령](#명령)
- [수신 모델](#수신-모델)
- [한계](#한계)
- [프로젝트 구조](#프로젝트-구조)
- [문서](#문서)

## 동작 방식

```
[제어 호스트]  --- adb (TCP 5555) --->  [Linux VM]
  kakao-cli (Python + cryptography)         redroid (Android 14, libndk ARM 변환)
                                           └ com.kakao.talk  (태블릿 지문으로 로그인)
```

- CLI는 Python 3.11+, `cryptography>=41`, `adb`가 필요하다. DB 수신은 AES-CBC로 메모리에서만 복호화한다. UI는 `uiautomator dump`로
  읽고 resource-id/좌표로 조작한다. 한글 입력은 **ADBKeyBoard IME** 경유.
- Android는 host 커널의 **binder**가 필요하다(전용 VM이 자체 커널로 제공).
- 카카오톡 네이티브 라이브러리는 ARM 전용 → x86_64에선 redroid의 **libndk 변환** 필요.

## 요구 사항

- **제어 호스트**: Python 3.11+, `adb`(android-tools). (Docker 이미지로도 제공 — 아래)
- **Android 호스트(VM)**: Linux + binder, Docker(redroid), 인터넷.
- 기존 폰과 공존하려면 redroid를 **허용 갤럭시탭(SM-X710 등)**으로 스푸핑해야 로그인 화면에
  "다른 기기와 함께 사용" 옵션이 뜬다. 상세는 [docs/setup-waydroid.md](docs/setup-waydroid.md)와
  [기기 지문](docs/initiatives/2026-10-16-tablet-fingerprint.md).

## 환경 구축

Proxmox VM에 redroid를 올리는 전체 절차는 **[docs/setup-waydroid.md](docs/setup-waydroid.md)**.
요약:

```bash
# VM 안에서 (binder 커널 모듈 필요: linux-modules-extra-$(uname -r))
sudo modprobe binder_linux devices=binder,hwbinder,vndbinder
sudo mount -t binder none /dev/binderfs
scripts/run-redroid.sh                 # 태블릿 지문으로 redroid 기동
# 로그인: 폰 카카오톡 → 더보기 → QR 스캔 (헤드리스에선 QR이 가장 간단)
```

재부팅 자동복구는 `scripts/kakao-redroid.service` + `scripts/kakao-boot.sh`를 설치하면 된다
(binder 로드·binderfs 마운트·컨테이너 기동·앱 실행·권한·IME까지 자동).

## 설치 / 실행

```bash
# 개발 설치
uv sync                      # 또는: pip install -e .
kakao doctor --connect 10.0.0.30:5555

# 소스로 바로 실행 (VM 배포 시)
PYTHONPATH=src python3 -m kakao_cli doctor --serial 127.0.0.1:5555
```

Docker로 OS 무관 실행:

```bash
docker build -t kakao-cli .
docker run --rm kakao-cli doctor --connect 10.0.0.30:5555
```

## 명령

| 명령 | 설명 |
|---|---|
| `doctor` | adb·디바이스·앱버전·현재 화면 점검 |
| `rooms` | 채팅방 목록(이름·최근메시지·시간·인원·안읽음) |
| `read [room|me]` | 최근 메시지 읽기 |
| `send <room> <text> --yes` | 텍스트 전송 |
| `memo <text>` | 나와의 채팅 전송(안전 테스트) |
| `send-file <room|me> <path> --yes` | 이미지·영상·문서(pdf/zip/doc) 전송 |
| `poll <room|me>` | 한 방의 신규 메시지 스트림(권장 수신, NDJSON) |
| `pull-media <dest> [--new-only]` | 송수신 미디어 수집(증분) |
| `watch` / `track` | (보조) 알림·전체목록 기반 수신 |
| `tui` | 대화형 터미널 UI(방 목록·실시간 읽기·전송) |
| `webui` | 웹 기기 콘솔: 화면·키보드·파일(채팅은 `tui`) |

공통 옵션: `--json`, `--serial`, `--connect HOST:PORT`, `--adb PATH`. 송신류는 `--home`으로
전송 후 앱을 백그라운드로 보낼 수 있다. 자세한 사용법은 `kakao <command> --help` 또는
`man kakao`(docs/kakao.1).

```bash
kakao send "엄마" "집에 가는 중" --yes
kakao send-file me ./report.pdf --yes      # 문서
kakao poll me --interval 3                 # 수신 스트림
kakao pull-media ./inbox --new-only
```

### 대화형 TUI

```bash
kakao tui --connect 10.0.0.30:5555
```
방 목록(←안읽음 뱃지) + 메시지 창 + 입력줄. **Tab** 포커스 전환, **↑↓/Enter** 방 선택,
입력줄에서 타이핑 후 **Enter** 전송(한글 OK), **q** 종료. 단일 기기라 한 번에 한 방을 본다.

### 웹 콘솔 (`kakao webui`)

웹 UI는 **기기 콘솔 전용**이다 — 채팅(방 목록·읽기·전송)은 **CLI/TUI**(`kakao tui`, `send`, `poll` …)
에서 한다. 제3자 의존·CDN 없이 stdlib `http.server`가 페이지를 메모리에서 서빙한다.

```bash
kakao webui --connect 10.0.0.30:5555           # http://127.0.0.1:8800
ssh -L 8800:127.0.0.1:8800 agent@192.168.0.30  # 원격 VM이면 SSH 터널 → 로컬 브라우저로 localhost:8800
```

VM에 소스로 배포해 LAN에서 상시 띄우는 예(무인증 — **신뢰하는 LAN 전용**, 공용망 노출 금지):

```bash
scp src/kakao_cli/*.py agent@192.168.0.30:kakao-cli/src/kakao_cli/
ssh agent@192.168.0.30 'tmux new-session -d -s kwebui \
  "cd ~/kakao-cli && PYTHONPATH=src python3 -m kakao_cli webui \
   --host 192.168.0.30 --port 8800 --allow-screen --serial 127.0.0.1:5555"'
# 접속: http://192.168.0.30:8800 (http → MSE 영상, 클립보드는 토스트/Ctrl+V 수동)
# 종료: ssh agent@192.168.0.30 tmux kill-session -t kwebui  (SIGHUP에도 jar·forward·잠금 정리)
```

콘솔 탭이 열려 가상 디스플레이를 쓰는 동안 UI 소스·송신류는 `console_busy`(종료 코드 7)로 거절된다.

```
┌ 안드로이드 /sdcard/… ⇪ ┬──────────────────────────────┐
│ ▸ Download  · a.pdf ⤓ │  Back  Home  Enter      2 fps │
│ [↓ 로컬로 복사]        │   ┌──────────────────┐       │
├ 로컬(VM) (루트)    ⇪ ─┤   │  기기 화면 스트림  │       │
│ · note.txt          ⤓ │   │ (클릭·드래그·키보드)│       │
│ [↑ 안드로이드로 복사]   │   └──────────────────┘       │
│ ✓ ↑ note.txt → …      │                              │
└───────────────────────┴──────────────────────────────┘
```

- **중앙 = 기기 화면**: 클릭=탭, 드래그=스와이프. 화면을 클릭하면 **키보드가 기기로** 간다(테두리 강조,
  **Esc**로 해제) — 글자는 ADBKeyBoard로(한글은 조합 완료 후 전송, ~150ms 묶음), Backspace/Enter/Tab/
  방향키/Home/End/PgUp/PgDn/Delete는 허용목록 키코드로. **Back/Home/Enter** 버튼. 재로그인(QR·보안코드·
  온보딩)이나 막힌 팝업 해제도 여기서 한다.
- **좌측 위 = 안드로이드 내부 저장소**(기본 `/sdcard/`), **좌측 아래 = 로컬 저장소** — "로컬"은 **webui가
  도는 호스트(VM)의 디스크**. 파일을 골라 **↓ 로컬로 복사**(adb pull) / **↑ 안드로이드로 복사**(adb push).
  **복사만**(이동·삭제·덮어쓰기 없음, 같은 이름이면 `이름-1.ext`, 폴더 미지원). 확장자 없는 수신 미디어
  블롭은 가져올 때 매직바이트로 확장자를 붙인다. 로컬 패널은 `--fs-root`(기본 `~`) 밖으로 못 나간다.
- **브라우저 ⇄ VM/기기 파일**: 패널의 **⇪ 올리기**(또는 드래그앤드롭)로 브라우저 파일을 그 폴더로 —
  로컬 패널은 VM 디스크, 안드로이드 패널은 VM에 잠시 스테이징 후 기기로 push. 파일 행의 **⤓**(또는
  더블클릭)는 브라우저로 받기(기기 파일은 `adb exec-out` 스트리밍). 업로드는 `.part`→충돌 없는 원자적
  배치, `--max-upload`(기본 2G)·여유공간 초과 시 거절, 진행률은 작업 줄에 `%`.
- **클립보드(기기 ⇄ 브라우저)**: scrcpy-server(아래)로 연동. 기기에서 복사하면 오른쪽 아래 토스트
  "기기 클립보드: … **[복사]**"(https/localhost 접속이면 **자동 복사** 옵션). 화면에 포커스가 있을 때
  **Ctrl/Cmd+V**: 텍스트는 기기 클립보드에 넣고 **포커스된 입력창에 붙여넣기**(줄바꿈 그대로, 타이핑보다
  정확), 이미지/파일은 기기 `/sdcard/Download`로 업로드(+미디어 스캔). **📋 기기로** 버튼은 보안
  컨텍스트에서 브라우저 클립보드를 읽어 같은 동작. 클립보드 내용은 서버 메모리에 최신 1개만 두고
  로그·디스크에 남기지 않는다(상태 폴링엔 번호만). scrcpy가 없으면 붙여넣기는 ADBKeyBoard 타이핑으로
  대체되고 기기→브라우저는 꺼진다. 상태바 `📋 연동`/`📋 —`.
- **scrcpy-server(제3자, Apache-2.0)**: v5.0.1 고정. 저장소에 넣지 않고 첫 사용 시 공식 GitHub 릴리스에서
  `~/.cache/kakao-cli/`로 받아 **sha256 고정 검증**(불일치면 거부). 오프라인은 `--scrcpy-server PATH`
  (역시 검증), 끄려면 `--no-scrcpy`. 기기 `/data/local/tmp`의 jar·adb forward·서버 프로세스는 webui
  종료 시(SIGTERM/SIGHUP 포함) 정리된다.
- **화면 = H.264 영상(scrcpy) + 가상 디스플레이**: 보는 사람이 있으면 scrcpy가 **브라우저 화면 영역
  크기의 가상 디스플레이**(CSS px × devicePixelRatio, dpi = 160 × dpr → 1dp ≈ 1 CSS px)를 만들고
  카카오톡을 그 위에서 띄운다. 데스크톱 창은 태블릿 2단 레이아웃, 폰은 폰 레이아웃으로 보이고, 창
  크기가 바뀌면 ~300ms 뒤 디스플레이도 따라 바뀐다(flex). **실제 기기 `wm size`/`wm density`는 절대
  건드리지 않는다.** 마지막 시청자가 떠나면 5초 뒤 가상 디스플레이를 없애고 앱은 기본 화면으로 돌아온다.
  상태바 `가상/미러` 선택: **미러**는 기본 디스플레이를 그대로(최대 `--video-max-size`, redroid 기본
  화면은 15Hz). 가상 디스플레이가 8초 안에 안 뜨면 자동으로 미러(상태바에 사유).
- **렌더러(자동 선택, 상태바 `자동/WC/MSE/PNG`·`?video=`)**: https/localhost(보안 컨텍스트)면
  **WebCodecs**(canvas), 평문 http면 **MSE**(서버가 자체 fMP4로 감싸 전송), 둘 다 안 되거나 스트림이
  실패하면 기존 **screencap PNG**(보는 동안만 캡처, ~2fps)로 자동 폴백. 입력은 보고 있는 화면으로 간다:
  영상이면 scrcpy 터치/키(가상 디스플레이 좌표), PNG면 adb(기본 디스플레이). 실측(redroid, 1400×860
  창): 활동 중 ~12–15fps·1.1–2.3Mbps, 정지 화면 0–5fps·수십 kbps, 탭→화면 변화 WebCodecs ~0.1–0.16s /
  MSE ~0.15–0.4s, 서버 CPU 2–3%, 기기는 SW 인코더(media.codec) ~17–27%.
- **CLI/TUI와 동시 사용**: 가상 디스플레이에 앱이 있는 동안 `input tap`은 기본 디스플레이로 가므로 CLI
  자동화가 엉뚱한 화면을 누르게 된다. 그래서 콘솔이 **기기에 잠금 파일**(5초 하트비트, 기기 시계 기준
  20초 지나면 무효)을 두고, 그동안 UI 수신·송신은 `console_busy`(종료 코드 7)로 거절한다.
  콘솔 탭을 닫거나 **미러**로 바꾸면 풀린다(`kakao doctor`에 표시). **DB 수신과 TUI 수신은 잠금 중에도 허용**한다.
- **모바일**: 좁은 화면(≤720px)에서는 기기 화면이 뷰포트 높이를 거의 채우고(세로 가상 디스플레이) 파일
  패널은 그 아래(스크롤). `100dvh`·safe-area·`visualViewport`, 입력 16px(iOS 확대 방지).
- **보안(중요)**: 콘솔은 **계정 전체 원격 조작**과 같다. 기본 바인드 `127.0.0.1`. `--host`가 로컬이 아니면
  콘솔 API가 **기본으로 꺼지고** `--allow-screen`을 명시해야 켜진다(기동 경고). 모든 콘솔 API는 Host
  허용목록(IP 주소·`localhost`·`--allow-host NAME`, DNS rebinding 차단), 조작 POST는 JSON + same-origin,
  업로드는 `X-Kakao-Op` 헤더 필수(CSRF 차단). 휴대폰에서는 `0.0.0.0`을 열지 말고 SSH 터널 또는
  Tailscale(`tailscale serve`로 https면 `--allow-host <이름>`)로 접속.

## 수신 모델

- **`poll <room>`(권장)**: 기본 `--source db`, 방의 `MAX(_id)` 이후 행을 읽는다.
  화면을 전환하거나 읽음 처리하지 않고 동일 본문·연속 수신을 행 ID로 구별한다.
  `rooms/read`도 DB 기본이며 `--source ui`로 기존 화면 경로를 선택한다.
  DB 버전/스키마/복호화 실패는 경고 후 UI로 폴백하며, 콘솔 잠금 중 UI 폴백은 거절한다.
  `read`의 기존 `messages`는 유지하고 상세 필드를 `message_details`로 추가한다.
  [암호 형식·읽음수·방 선택·종류 표](docs/db-receive.md)를 참고한다.
- **`watch`(보조)**: OS 알림(`dumpsys notification`)을 읽는다. **앱이 백그라운드**여야
  알림이 뜬다(포그라운드면 즉시 읽음 처리). 방 무관하게 잡지만 음소거/포커스 방은 놓친다.

## 한계

- UI 자동화라 **앱 업데이트로 레이아웃/resource-id가 바뀌면 깨질 수 있다**(셀렉터는 라이브
  `uiautomator dump`가 기준).
- 한 기기에 **두 제어 세션을 동시에** 돌리면 UI 경합이 난다(poll 중 send 등). 순차 사용 권장. 웹 콘솔이
  가상 디스플레이로 앱을 쓰는 동안 UI 경로와 송신은 `console_busy`로 거절된다(위 웹 콘솔 참고).
- Play Integrity/anti-emulator로 로그인·동작이 막힐 가능성(현재 빌드에선 동작 확인).
- 과도한 자동화는 계정 제한으로 이어질 수 있다.

## 프로젝트 구조

```
src/kakao_cli/
  cli.py        명령/인자 파싱, 출력
  adb.py        adb transport(subprocess): shell/tap/type(ADBKeyBoard)/push/dump
  ui.py         uiautomator XML 파싱 + 셀렉터(Screen/Node)
  driver.py     고수준 플로우: rooms/read/send/send_file/send_doc/poll/open_*
  notify.py     알림 기반 수신 파서(watch)
  media.py      미디어 블롭 pull + 매직바이트 타입
  errors.py     구조화 오류 코드 / 종료 코드
  webui.py      웹 콘솔(stdlib HTTP): 화면·입력·파일·클립보드·영상 API + 페이지
  scrcpy.py     scrcpy-server 고정/검증·프로토콜·세션(제어+영상 소켓)
  video.py      영상 허브(시청자 팬아웃·가상 디스플레이 크기/flex·미러 폴백)
  fmp4.py       MSE용 최소 fMP4 muxer
  consolelock.py 기기측 콘솔 잠금(CLI/TUI 거절)
  ioutil.py     JSON/텍스트 출력
scripts/        run-redroid.sh, kakao-boot.sh, kakao-redroid.service, provision-waydroid.sh
docs/           setup-waydroid.md, kakao.1(man), initiatives/*(설계·분석·원장)
completions/    bash/zsh 자동완성
```

## 문서

- 설계: [docs/initiatives/2026-10-16-design.md](docs/initiatives/2026-10-16-design.md)
- 환경 구축: [docs/setup-waydroid.md](docs/setup-waydroid.md)
- man page: `man -l docs/kakao.1`
- 수집·분석 원장: [docs/initiatives/2026-10-16-artifact-collection.md](docs/initiatives/2026-10-16-artifact-collection.md)
- 선행 사례 분석: [docs/initiatives/2026-10-16-prior-art.md](docs/initiatives/2026-10-16-prior-art.md)

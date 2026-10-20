# kakao-cli

공식 Android 카카오톡을 **redroid(Docker) / Waydroid**에서 구동하고 **adb UI 자동화**로
제어하는 Linux CLI. 서버 프로토콜(LOCO)을 재구현하지도, 로컬 DB를 복호화하지도 않는다 —
공식 앱을 그대로 조작한다.

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
  kakao-cli (Python, 순수 stdlib)         redroid (Android 14, libndk ARM 변환)
                                           └ com.kakao.talk  (태블릿 지문으로 로그인)
```

- CLI는 `adb` 바이너리만 있으면 어디서든 동작(제3자 의존 없음). UI는 `uiautomator dump`로
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
| `webui` | 같은 UI를 브라우저로(stdlib HTTP, 기본 localhost) |

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

### 웹 UI

TUI와 같은 톤앤매너(모노스페이스·다크·역상 바)를 브라우저로 그대로 옮긴 것. 제3자 의존
없이 `http.server`만 쓰고, 페이지(HTML/CSS/JS)는 메모리에서 서빙한다. TUI와 동일하게
단일 백그라운드 워커가 Driver를 소유해 기기 접근을 직렬화하고, 브라우저는 1초마다
`/api/state`를 폴링한다.

```bash
kakao webui --connect 10.0.0.30:5555           # http://127.0.0.1:8800
# 원격(헤드리스 VM)에서 볼 때는 SSH 터널을 권장:
ssh -L 8800:127.0.0.1:8800 agent@192.168.0.30  # 그 뒤 로컬 브라우저로 localhost:8800
```

키맵은 TUI와 동일하다(**Tab** 포커스, **↑↓/Enter** 방 선택, **Enter** 전송, **Esc** 목록,
**r** 새로고침). 입력줄 왼쪽 **`+`**로 파일/이미지를 첨부한다(브라우저 파일 선택 →
업로드 → 열린 방으로 `send-file`과 같은 경로로 전송: 이미지·영상은 공유 피커, 문서는
방 안 `+` → 파일). 이미지 **붙여넣기**·**드래그앤드롭**도 같은 업로드 경로를 쓴다.

메신저형 편의:
- **방 목록 2줄 행**: `이름 (인원) … 시각` / dim 미리보기 + 안읽음 배지.
- **입력은 textarea**: Enter 전송, Shift+Enter(또는 Ctrl+Enter) 줄바꿈, 방별 초안 자동 보존.
- **빠른 전환**: **`/`** 또는 **Ctrl+K**로 방 이름 퍼지 필터 → Enter로 열기.
- **탐색 키**: **Alt+↑/↓** 이전/다음 방, **Alt+Shift+↑/↓** 안읽음 방만. **`?`** 도움말.
- **탭 제목 배지** `(N) kakao`(조용한 방 제외), **`m`** 현재 방 조용히 토글, **`n`** 탭이
  숨겨졌을 때만 뜨는 opt-in 알림(권한은 토글 시에만 요청).
- 입력창에 포커스가 있을 때 단일 키(`r` 등)는 동작하지 않는다.

메시지는 **IRC형** 한 줄(`보낸사람 │ 본문`)로 보여 그룹방도 읽기 쉽다 — 같은 사람이 연속으로
보내면 이름을 `·`로 생략하고, 내 메시지는 `나`와 `--me` 색으로 구분한다(정렬은 그대로).
보낸사람 이름은 해시 기반 6색으로 칠한다. 날짜가 바뀌면 `──── <날짜> ────` 구분선이 들어가고
스크롤 중 상단에 고정된다. (카카오톡이 uiautomator에 **시각·읽음수를 텍스트로 노출하지 않아**
그 두 가지는 표시하지 않는다 — 추측하지 않는다.)

**수신 미디어/파일 받기**: 메시지 창의 사진·영상·파일 말풍선은 **`⤓`** 로 표시되고 클릭하면
브라우저로 내려받는다. 서버는 앱을 구동해 그 말풍선을 열고 **Save**로 기기에 저장시킨 뒤
(사진→`Pictures/KakaoTalk`, 문서→`Download/KakaoTalk`) 그 파일만 pull해 원래 이름·타입으로
전송한다 — DB 복호화나 chatId 추정 없이 **열린 방의 보이는 항목만** 다루므로 안전하다.

기본 바인드는 `127.0.0.1`이며 인증이 없다 — `--host 0.0.0.0`은 네트워크
전체에 계정 제어를 노출하므로 경고를 출력한다. 가능하면 SSH 터널을 쓴다.

#### 콘솔 모드 (기기 화면 + 파일 매니저)

상단 **[채팅 | 콘솔]** 전환(또는 **`c`**, `#console` 링크)으로 여는 두 번째 모드. 기본은 채팅.

```
┌ 안드로이드 /sdcard/… ─┬──────────────────────────────┐
│ ▸ Download  · a.pdf   │  Back  Home  Enter           │
│ [↓ 로컬로 복사]        │   ┌──────────────────┐       │
├ 로컬(VM) (루트) ──────┤   │  기기 화면 스트림  │       │
│ · note.txt            │   │ (클릭·드래그·키보드)│       │
│ [↑ 안드로이드로 복사]   │   └──────────────────┘       │
│ ✓ ↑ note.txt → …      │                              │
└───────────────────────┴──────────────────────────────┘
```

- **중앙**: 기기 화면 실시간 스트림. 클릭=탭, 드래그=스와이프. 화면을 클릭하면 **키보드가 기기로**
  간다(테두리 강조, **Esc**로 해제) — 글자는 ADBKeyBoard로(한글 조합 완료 후 전송, ~150ms 묶음),
  Backspace/Enter/Tab/방향키/Home/End/PgUp/PgDn/Delete는 허용목록 키코드로.
- **좌측 위 = 안드로이드 내부 저장소**(기본 `/sdcard/`), **좌측 아래 = 로컬 저장소** — 여기서 "로컬"은
  **webui가 도는 호스트(VM)의 디스크**다(브라우저는 노트북 파일시스템을 볼 수 없다). 파일을 골라
  **↓ 로컬로 복사**(adb pull) / **↑ 안드로이드로 복사**(adb push). 진행 상황은 아래 작업 줄에 표시.
- **복사만 한다**: 이동·삭제·덮어쓰기 없음(같은 이름이면 `이름-1.ext`). 폴더는 복사하지 않는다.
  확장자 없는 수신 미디어 블롭(`Android/data/com.kakao.talk/contents/…`)은 가져올 때 매직바이트로
  확장자를 붙인다.
- 로컬 패널은 **`--fs-root`(기본 `~`) 밖으로 나갈 수 없다**(realpath 격리, 바깥을 가리키는
  심볼릭링크 숨김). 콘솔 전체(화면·키보드·파일)는 화면 API와 같은 게이트(로컬 바인드 또는
  `--allow-screen`) + JSON/same-origin 검사를 받는다.
- **브라우저 ⇄ VM/기기 파일**: 각 패널의 **⇪ 올리기**(또는 패널 위로 드래그앤드롭)로 브라우저 파일을
  그 폴더에 올린다 — 로컬 패널은 VM 디스크로, 안드로이드 패널은 VM에 잠시 스테이징 후 기기로 push.
  파일 행의 **⤓**(또는 더블클릭)는 브라우저로 내려받기(안드로이드 파일은 `adb exec-out` 스트리밍,
  VM 디스크 경유 없음). 업로드는 raw body 스트리밍 → `.part` → 이름 충돌 없는 원자적 배치,
  `--max-upload`(기본 2G)와 여유공간을 넘으면 거절. 진행률은 작업 줄에 `%`로.
- **Host 허용목록**: 콘솔 API는 IP 주소·`localhost`로 접속한 요청과 `--allow-host NAME`(예:
  `tailscale serve` 이름)만 받는다(DNS rebinding 차단). 업로드는 `X-Kakao-Op` 헤더 필수(CSRF 차단).
- 콘솔이 열려 있는 동안 워커가 캡처 모드라 **채팅 갱신은 멈춘다**(채팅으로 돌아오면 재개). 큰 파일
  복사 중엔 같은 adb 연결을 쓰므로 화면 fps가 잠깐 떨어진다.

#### 기기 화면 보기·조작 (로그인용)

웹 UI에서 태블릿(카카오톡) 화면을 실시간에 가깝게 보고 **탭·스와이프·텍스트·Back/Home/Enter**로
조작한다 — QR 스캔·보안코드 입력·"Use with Main Device" 체크·온보딩 같은 재로그인 플로우와,
막힌 팝업/권한 창 해제에 쓴다. 상단/하단 바의 **⧉ 화면** 버튼(또는 데스크톱 **`g`**)으로 연다.
로그인·온보딩 화면이 감지되면 상단에 **"로그인이 필요합니다 — [화면 열기]"** 배너가 뜬다.

- 구현: 제3자 의존 없이 `adb exec-out screencap -p`를 워커가 **보는 사람이 있을 때만** 캡처(최신
  1프레임만 메모리 캐시, **디스크에 저장하지 않음**). 브라우저는 작은 `/api/screen/info`로 캡처
  seq를 보고 **바뀐 프레임만** `<img>`로 받는다(동일 프레임 재전송 안 함). 실측 **~1–3fps**, 프레임
  1600×2560 PNG **~0.4–0.6MB**. 모든 조작은 기존 Worker 큐로 직렬화하며, 화면 뷰가 열린 동안에는
  채팅 폴링을 멈춰 adb 경합을 피한다.
- 좌표: 클릭을 이미지 레터박스를 고려해 기기 좌표로 변환한다(screencap은 현재 회전 상태로 나오므로
  `input`과 같은 좌표계 → 추가 회전 불필요).
- **보안(중요)**: 화면 보기·조작은 **계정 전체 원격 조작**과 같다. 기본 바인드 `127.0.0.1`을
  유지하고, `--host`가 로컬이 아니면 화면 API를 **기본으로 끈다**. 비로컬에서 켜려면
  **`--allow-screen`**을 명시해야 한다(더 큰 위험, 기동 경고에 표시). 조작 API는 POST + JSON
  Content-Type만 받고 Origin이 있으면 Host와 일치해야 한다(CSRF 차단). 되도록 SSH 터널/Tailscale로.

#### 모바일 웹

같은 PAGE가 휴대폰에서도 동작한다(반응형, 제3자 의존 없음). 좁은 화면(≤720px)에서는
**방 목록 ↔ 대화 화면 단일 전환**으로 바뀐다 — 방을 탭하면 대화 화면으로 가고, 타이틀 바의
**[←]** 또는 **브라우저 뒤로가기**로 목록에 돌아온다. 입력줄에 **[보내기]** 버튼이 있고(모바일
키보드 Enter와 함께 동작), `+`·`⤓`는 터치 크기(44px)다. `100dvh`·safe-area·`visualViewport`로
가상 키보드가 올라와도 입력줄이 보이며, 입력 글씨는 16px라 iOS에서 확대되지 않는다. 위로
스크롤해 과거를 보는 중 새 메시지가 오면 위치를 유지하고 **"새 메시지 ↓"** 알림만 띄운다
(데스크톱도 동일).

휴대폰에서 접속하려고 `--host 0.0.0.0`을 기본으로 쓰지 말 것. 권장 경로:
- **SSH 로컬 포워딩**: 폰에서 쓰기 어렵다면 노트북에서 터널을 열고 테더링/같은 망으로 접속.
- **Tailscale 등 사설 VPN**: VM에 깔고 tailnet 안에서 `http://<tailscale-ip>:8800` 으로 접속.

어느 경우든 공용 네트워크에 노출하지 않는다(무인증).

## 수신 모델

- **`poll <room>`(권장)**: 한 방을 열어 보이는 메시지(텍스트 + `[Photo]`/`[Video]`)를 폴링,
  신규만 emit. 알림/백그라운드 의존이 없어 견고하고 음소거 방도 잡는다. 외부(폰) 발신이므로
  adb 경합이 없다. 단점: 한 번에 한 방, 폴링 간격 내 연속 메시지는 합쳐질 수 있다.
- **`watch`(보조)**: OS 알림(`dumpsys notification`)을 읽는다. **앱이 백그라운드**여야
  알림이 뜬다(포그라운드면 즉시 읽음 처리). 방 무관하게 잡지만 음소거/포커스 방은 놓친다.

## 한계

- UI 자동화라 **앱 업데이트로 레이아웃/resource-id가 바뀌면 깨질 수 있다**(셀렉터는 라이브
  `uiautomator dump`가 기준).
- 한 기기에 **두 제어 세션을 동시에** 돌리면 UI 경합이 난다(poll 중 send 등). 순차 사용 권장.
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

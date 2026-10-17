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

**수신 미디어/파일 받기**: 메시지 창의 사진·영상·파일 말풍선은 **`⤓`** 로 표시되고 클릭하면
브라우저로 내려받는다. 서버는 앱을 구동해 그 말풍선을 열고 **Save**로 기기에 저장시킨 뒤
(사진→`Pictures/KakaoTalk`, 문서→`Download/KakaoTalk`) 그 파일만 pull해 원래 이름·타입으로
전송한다 — DB 복호화나 chatId 추정 없이 **열린 방의 보이는 항목만** 다루므로 안전하다.

기본 바인드는 `127.0.0.1`이며 인증이 없다 — `--host 0.0.0.0`은 네트워크
전체에 계정 제어를 노출하므로 경고를 출력한다. 가능하면 SSH 터널을 쓴다.

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

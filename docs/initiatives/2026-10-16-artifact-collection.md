# 카카오톡 바이너리 수집 및 분석 원장

목표: 공식 배포물(Windows exe, macOS dmg, Android apk)을 수집하고 정적 분석해, CLI 클라이언트에 필요한 프로토콜(LOCO/REST)과 인증 흐름을 정리한다.

아티팩트 위치(저장소 밖): `/home/agent/kakao-artifacts/`

## 수집 현황

| 플랫폼 | 출처 | 파일 | 크기 | SHA-256 | 상태 |
|---|---|---|---|---|---|
| Windows x64 | `https://app-pc.kakaocdn.net/talk/win32/x64/KakaoTalk_Setup.exe` (Last-Modified 2026-10-07) | `win-x64/KakaoTalk_Setup.exe` | 93,820,712 | `4b904622d873b630ba78c291748bfdcb4c51b0cc7a37d3393632fcff042fa723` | 수집 완료 |
| Windows x86 | `https://app-pc.kakaocdn.net/talk/win32/KakaoTalk_Setup.exe` (Last-Modified 2026-10-07) | `win32/KakaoTalk_Setup.exe` | 83,205,208 | `f3b21b92dbe17f13e8606a91df2e2e32ef4ffd4054b007242e6be32e45bf7e2c` | 수집 완료 |
| macOS | `app-pc.kakaocdn.net/talk/mac/KakaoTalk.dmg` 등 추정 경로는 403. 현재 공식 배포는 Mac App Store(id869223134)로 보임 | - | - | - | 미수집 |
| Android | Google Play 전용. 공식 직접 다운로드 경로 없음 | - | - | - | 미수집 |

## 관찰 (Observation)

- 두 Windows 설치 파일 모두 `PE32 GUI, Nullsoft Installer self-extracting archive`(`file`).
- Authenticode PKCS#7에 서명자 `CN=Kakao Corp., O=Kakao Corp., C=KR`와 DigiCert Trusted G4 Code Signing CA 체인이 있다(`openssl pkcs7 -print_certs`).
  - 한계: 인증서 체인이 있다는 것만 확인했다. 파일 다이제스트와 서명이 맞는지는 아직 검증하지 않았다(osslsigncode 없음).

- x64 설치 파일 해제(7-Zip 26.04): 9,266개 파일, 앱 버전 26.8.2(`ChangeLogs_ko.md`). `KakaoTalk.exe`는 `PE32+ x86-64 GUI`. ASAR, CEF, JS가 없는 **네이티브 앱**이다.
- `KakaoTalk.exe`, `Vox3.dll`, `CrashReporter.exe`는 **Themida로 보호**되어 있다. `.themida`(raw 0) / `.boot` / `.vm_sec` 섹션이 있고, 이름 없는 코드·데이터 섹션의 엔트로피가 7.77~7.99다(`~/kakao-tools/pe_sections.py`).
  - 같은 이유로 평문 문자열이 없다. 호스트명, LOCO 명령명(CHECKIN, LOGINLIST 등), `https://` 모두 0건.
  - 추론: Windows 바이너리는 정적 분석이 사실상 불가능하다. 런타임 언패킹이나 Windows에서의 트래픽 관찰이 필요하다. → 주 정적 분석 대상은 APK로 옮긴다.

## 분석 환경

- REA 6.5.0 MCP 연결됨. Ghidra 12.1.4 사용 가능.
- `~/kakao-tools`에 로컬 설치(2026-10-16): 7-Zip 26.04, jadx 1.5.6, apktool 3.0.3, jadx-headless-mcp 0.7.1 JAR, playwright-core 1.64.1(기존 Playwright Chromium 사용).
  - REA Android 도구를 쓰려면 MCP 서버 환경에 `REA_JADX_MCP_JAR=/home/agent/kakao-tools/jadx-headless-mcp-0.7.1-all.jar`를 넣고 재시작해야 한다. 그 전에는 jadx CLI를 직접 쓴다.
  - apkeep 1.1.0은 GLIBC 2.38이 필요한데 이 호스트는 2.35라 실행되지 않는다.
- APKMirror는 curl로 받으면 Cloudflare 챌린지에 막힌다. headless Chromium으로는 통과한다(`~/kakao-tools/scripts/apkmirror*.js`). APKPure 직접 링크는 403.

## 선행 사례 (외부, 미검증)

- node-kakao: LOCO 프로토콜 호환 라이브러리(MIT). 오래돼서 현재 버전에서는 동작하지 않을 수 있다.
- openkakao-rs: REST+LOCO CLI. KakaoTalk 로컬 캐시에서 자격 증명을 가져오는 방식.
- matrix-appservice-kakaotalk: node-kakao 기반 브리지.

## 위험

- 비공식 클라이언트 사용은 계정 이용 제한으로 이어질 수 있다(node-kakao README 경고).

## 미해결 질문

1. Windows 클라이언트는 네이티브(C++)인가, Electron/CEF인가? → NSIS 해제 후 확인.
2. 현재 LOCO 버전과 핸드셰이크(booking/checkin, 암호화 방식)는?
3. PC 로그인 흐름(이메일/비밀번호 + 기기 등록 인증 코드)과 REST 엔드포인트는?

## 2026-10-16 추가: Android 분석 & 런타임 결정

### Android APK (분석 가능 — 주 참조 대상)
- `com.kakao.talk` 26.8.3(versionCode 29260830), compileSdk 37, 서명 `OU=kakaoteam, O=kakao`(apksigner v3 통과).
- **네이티브 라이브러리 ARM 전용**: arm64-v8a, armeabi-v7a만. x86/x86_64 split 없음 → x86_64 Waydroid 구동 시 ARM 변환 레이어(libndk_translation/libhoudini) 필요.
- 런처: `com.kakao.talk.activity.SplashActivity`. dex 평문 문자열에 LOCO 명령명(LOGINLIST/CHECKIN/GETCONF/WRITE/SYNCMSG), LOCO 서버(booking-loco.kakao.com, ticket-loco.kakao.com, katalk.kakao.com), REST(`/mac/*.json`, X-VC) 존재.
- 자동화 진입점(exported 컴포넌트, intent 기반 — UI 탭보다 견고):
  - `RecentExcludeIntentFilterActivity`: ACTION_SEND/SENDTO/SEND_MULTIPLE/VIEW, mime text/image/video/audio/* → 공유 시트로 특정 채팅에 전송 가능.
  - `SplashConnectActivity`: VIEW + `ENTER_CHAT_ROOM`.
  - 딥링크 스킴: kakaotalk, kakaolink, kakaoopen, kakaotalkqrcode 등.
  - `NotificationActionService` 등 알림 관련 컴포넌트.

### 런타임 구동 가능성 (이 샌드박스 실측)
- 이 환경은 **비특권 LXC**(Proxmox). binder/ashmem 커널 모듈·`/dev/binder` 없음 → **Waydroid 구동 불가**. wine 저장소 버전 6.0.3(2021)뿐, GUI 없음(Xvfb만).
- **결정**: 실제 구동은 Proxmox 호스트에 **별도 VM/CT**를 띄워서 함. 이 샌드박스는 디컴파일 참조·코드 작성·네트워크 가능 작업 전용(UI 자동화 라이브 검증은 여기서 불가).

### 통합 방식 결정 로그
- LOCO 직접접속: 계정차단 실증 → 비채택(연구 opt-in 후보).
- 로컬 DB 복호화: 26.5.0+ 봉인 + Linux엔 읽을 DB 없음 → 비채택.
- **UI 자동화로 확정.** 기반 후보 비교:
  - Android/Waydroid + adb(UIAutomator): 구조화된 접근성 트리·리소스ID·intent 진입점·알림 조회 → 견고. 단 ARM 변환 필요.
  - Windows/Wine: x86_64 네이티브 실행(ABI 변환 불요)이나 **Themida anti-Wine 거부 위험** + 스킨 커스텀 드로잉이라 UIA 트리 없음 → 좌표/OCR 자동화만 가능(취약).
  - → 자동화 견고성에서 **Android/Waydroid 권장**. 기반 최종 선택 대기 중.

### 기반 최종 결정 (2026-10-16)
- **Android 카카오톡 + Waydroid + adb UI 자동화**로 확정. Windows/Wine 비채택(Themida anti-Wine 도박 + 커스텀 드로잉으로 UIA 트리 부재).

### 태블릿 공존 로그인 제약 (2026-10-16)
- 기존 폰과 동시 사용하려면 Waydroid가 **태블릿/서브기기**로 인식돼야 함(아니면 폰 로그아웃).
- 카카오톡 태블릿 공존은 **갤럭시탭 allowlist를 서버가 게이트**. dex 근거: `sub_device`/`subDevice`/`SUB_DEVICE`, `isTablet`, `deviceType`, `useSub`, `companion`, 모델 `SM-T230`/`SM-T580`/`SM-X900`. "다른 기기와 함께" 문구는 코드에 없음(서버 판정).
- 대응: Waydroid build.prop을 허용 갤럭시탭으로 스푸핑 + characteristics=tablet + sw600dp+ 디스플레이. 로그인 화면에서 옵션 표시 여부로 실측 확정. 상세는 design.md §3.1.

### 홈랩 프로비저닝 (2026-10-16)
- 호스트 Proxmox `.100`(prxmx), API 토큰 `agent@pve!homelab`로 제어. CT 101=현재 머신(binder 없음).
- Ubuntu 24.04 cloud 이미지 `local:iso/noble-server-cloudimg-amd64.img`(597MB) 다운로드 완료.
- **VM 108 `waydroid-kakao` 생성**: 4코어/6GB/cpu=host/q35, net0 vmbr0, 정적 IP **192.168.0.30/24 gw .1**, cloud-init(ciuser=agent, CT101 ed25519 키), serial 콘솔. OS 디스크(scsi0) 미부착.
- **막힌 지점**: 임의 경로 디스크 import는 PVE에서 **root 전용**(`Only root can pass arbitrary filesystem paths`). 토큰은 storage content 변경(Datastore.Allocate)도 불가. → OS 이미지를 디스크에 넣는 1단계만 호스트 root 핸드오프 필요. 이후(scsi0 연결·resize·start·게스트 SSH·Waydroid 설치) 전부 토큰+SSH로 진행 가능.

### VM 108 부팅 & Waydroid 설치 (2026-10-16)
- root가 `qm disk import` 수행 → 토큰으로 scsi0 연결(discard/ssd), boot=scsi0, 48G 리사이즈, 기동.
- VM: Ubuntu 24.04.5, 커널 6.8.0-142, 4코어/5.9GB, /dev/kvm 존재, 디스크 46G. SSH `agent@192.168.0.30`(CT101 ed25519 키).
- **binder 가용 확인(핵심 관문 통과)**: cloud 커널엔 binder 모듈 누락 → `linux-modules-extra-$(uname -r)` 설치 후 `binder_linux` 로드, binderfs 마운트 성공(binder/hwbinder/vndbinder). `/etc/modules-load.d`+`modprobe.d`로 영구화.
- Waydroid 1.6.2 설치. `waydroid init -s GAPPS` 진행 중(LineageOS 20 GAPPS x86_64 이미지).

### redroid 전환 & 카카오톡 구동 성공 (2026-10-16) ★돌파
- Waydroid→**redroid(Docker)** 전환. VM 108에 Docker 29.9.1 설치, `redroid/redroid:14.0.0-latest` 구동.
- 태블릿 스푸핑 인자로 실행: model SM-X710/samsung/gts9wifi/characteristics=tablet, 1600x2560@340. ABI에 arm64-v8a 포함(**libndk ARM 변환 내장 작동**).
- 카카오톡 26.8.3 split 사이드로드 성공(install-multiple, primaryCpuAbi=arm64-v8a).
- **앱 정상 실행**(초기 1회 세그폴트는 있었으나 재기동 시 안정), AuthenticatorActivity 로그인 화면 도달.
- **★서버 allowlist 통과**: 로그인 화면에 "Use with Main Device"(다른 기기와 함께 사용) + `chk_subdevice` 체크박스 노출 → 폰 공존 서브기기 로그인 가능. SM-X710 지문이 먹힘.
- 로그인 자동화 계약(resource-id): 계정 `account_key>edit_text`, 비번 `password>edit_text`, 공존 체크 `chk_subdevice`, 로그인 `login_kakao_account`, QR 로그인 `signup_kakao_account_with_qr`.
- 미해결: adb를 제어 호스트(CT101)로 노출(현재 VM 0.0.0.0:5555 매핑). CT101엔 adb 없음 → Docker 클라이언트 또는 adb 설치 필요. 초기 세그폴트 원인(안정성) 추적.

### ★★ 엔드투엔드 검증 완료 (2026-10-16)
- QR 로그인으로 **태블릿 서브기기 로그인 성공**(폰 유지). 온보딩(채팅복원 Skip, 권한, 백그라운드 허용) 통과 → MainActivity 진입, 실제 친구/채팅 목록 로드.
- 채팅 목록 계약: `recycler_view` 행 = `name`/`message`/`time`/`members_count`. 하단탭은 content-desc("Chats tab" 등)로 선택.
- 채팅방 계약(라이브 확정, APK 정적 후보와 다름): 입력 `message_edit_text`, 전송 `send_layout`, 메시지목록 `chat_log_recycler_list`, 미디어 `media_send_layout`.
- **나와의 채팅(안전 전송 대상)**: Friends탭 → 상단 내 아바타 → "My Chatroom".
- **실제 전송+readback 검증 성공**: 자기채팅에 ASCII 테스트 메시지 전송 후 XML readback으로 확인(READBACK-OK). 전 파이프라인 작동.
- TODO: 한글/유니코드 입력은 `adb shell input text`로 불가 → ADBKeyBoard IME 필요. 제어는 VM에서 직접(CT101 adb 없음).

### 수신 watch / 재부팅 자동기동 (2026-10-16)
- watch: `dumpsys notification --noredact` 파서(notify.py)로 수신 이벤트 NDJSON. 자기발신 미알림→수신전용. 실수신 검증은 폰 테스트 메시지 필요.
- read 보정: message/text/text_with_pin 노드 수집(연속 전송은 말풍선 병합되어 합쳐질 수 있음).
- **재부팅 자동기동**: 커널 업데이트(6.8.0-142→147)로 binder 모듈 유실 → `linux-modules-extra-$(uname -r)` 필요. systemd `kakao-redroid.service` + `/usr/local/bin/kakao-boot.sh`가 binder 로드·binderfs 마운트·컨테이너 start(restart=no, systemd 소유)·앱 기동(콜드스타트 세그폴트 재시도)·IME 재확인. 컨테이너 /data 볼륨으로 **로그인·IME·권한 영속**(재로그인 불필요). 재부팅 2회로 완전 자동복구 검증 완료.
- TODO(요구 추가): 첨부/이미지/영상 파일 송수신 파이프라인.

### 파일 파이프라인 — 진행 (2026-10-16)
- 전송 메커니즘 검증: `adb push`→MediaStore 등록(MEDIA_SCANNER_SCAN_FILE)→카카오톡 미디어 권한(READ_MEDIA_IMAGES/VIDEO, **부여+앱재시작 필요**) 후 방 내부 미디어버튼(`media_send_layout`)→피커(bottom sheet: `rv_media` 썸네일, `btn_send`). 권한 없으면 "No access allowed to photos".
- ACTION_SEND 공유 경로: 명시적 컴포넌트(RecentExcludeIntentFilterActivity) 지정은 MainActivity로 빠짐 → 수신자 피커 진입 액티비티 재확인 필요.
- 미디어 권한 grant를 kakao-boot.sh에 추가(영속).
- 남은 작업: 피커 썸네일 선택 UI 정밀 매핑(최신=첫 썸네일 활용) + btn_send로 send-file 명령화; 영상/임의첨부; 수신 파일(알림/스토리지에서 pull) 파이프라인.

### 파일 파이프라인 완성 (2026-10-16)
- **send-file(이미지/영상)**: ACTION_SEND 공유 인텐트로 정확한 파일 첨부(content URI, 갤러리권한 불필요) → 수신자 피커(`chat_room_list_item` desc로 이름/me 매칭 선택) → `submit_button`. 자기채팅 이미지 실전송 검증.
- **pull-media(수신/수집)**: 미디어는 `/sdcard/Android/data/com.kakao.talk/contents/<cat>/<chatId>/<hh>/<sha256>`에 확장자 없는 content-addressed 블롭으로 저장. ext_data_rw로 접근(adb root도 가능). pull 후 매직바이트로 타입판별(jpg/png/gif/mp4/webp). 12개 블롭 pull 검증(chat_id 포함).
- 후속: pull-media `--new-only`(seen 해시 상태파일) 증분 수신, chat_id↔방이름 매핑, 임의 문서(pdf/zip) 전송(앱 파일피커 경로), watch와 미디어 연관.

### watch 실수신 검증 완료 (2026-10-16)
- 사용자 폰에서 여러 채팅 전송 → **동기화는 됨**(채팅목록 갱신), 그러나 **앱 포그라운드면 읽음처리로 알림 0건**.
- **앱 백그라운드(HOME)** 상태에서 수신 알림 정상 발생. 알림 채널 `new_message_v1`(importance 4). parse가 `android.title`(방)·`android.text`(메시지) 추출 검증: 실제 2건 정확 추출, null 요약 알림 필터.
- 운영 결론: 수신 상시 감지는 **앱 백그라운드** 전제. kakao-boot.sh가 기동 후 HOME으로 수신대기 상태로 둠. send/read는 일시적으로 포그라운드 → 이후 HOME 복귀 필요(후속: send류 종료 시 HOME).

### poll 수신 실검증 (2026-10-16)
- 사용자 방향: 알림 포기, "방 선택→폴링+송신". `poll <room|me>` 구현.
- 폰에서 "나와의 채팅"에 보낸 메시지를 `poll me`가 실시간 포착: `{"room":"me","text":"gg"}`.
- 텍스트+미디어([Photo]/[Video]) 모두 추출. 단일 방 포그라운드라 알림/백그라운드 의존 없음. 외부(네트워크) 발신이므로 adb 동시성 충돌 없음(같은 기기 2 CLI 동시제어만 충돌).
- 보너스 확정: 오픈채팅은 하단 "Now" 탭 하위. unread_count 목록에서 읽힘.

### 후속 3건 (2026-10-16)
- **send류 --home**: send/memo/send-file에 `--home` 추가 → 송신 후 앱 백그라운드(수신대기/연속 poll 보전용). open_self_chat 3회 재시도로 탐색 안정화(이전 "profile avatar not found" 간헐실패 해결). memo --home 검증(verified + 런처 복귀).
- **pull-media --new-only**: `<dest>/.seen.json`에 해시 영속 → 증분 수신. 1차 18개, 2차 0개 검증.
- **문서(pdf/zip) 전송 — 한계 확인**: SEND 핸들러가 application/*을 선언하나, RecentExcludeIntentFilterActivity는 **image/video만 수신자 피커로 라우팅**(app/*, */*, octet-stream 모두 피커 미노출, MainActivity로 빠짐). 앱 내 미디어 시트에도 '파일' 옵션 없음(Camera/Photo/Location/Music/GiftShop/MiniGames). → 문서 전송은 미지원으로 명시(send_file이 명확한 에러). 후속: 파일매니저→Android 공유 시트→KakaoTalk 경로 자동화 조사. 이미지/영상은 정상.

### 문서 전송 해결 + 완성도 고도화 (2026-10-16)
- **문서(pdf/zip/doc) 전송 성공**: 사용자 힌트대로 입력창 '+' → 시트 스크롤 → "File" → "Select from File" → DocumentsUI(system picker)에서 파일명 검색·선택 → 방 전송. pdf·zip CLI 실전송 검증. send_file이 확장자로 이미지/영상=공유인텐트, 문서=send_doc(파일피커) 라우팅.
- **ensure_main 견고화**: 이물 foreground(DocumentsUI/다이얼로그) back 복구 + 최후 force-stop 재기동. open_self_chat 간헐 실패 근본 해결.
- **폴리싱**: 명령별 rich --help(설명·메타바·예시), README.md 종합, docs/kakao.1 man page(경고 0), completions/{kakao.bash,_kakao} bash·zsh 자동완성, pyproject 메타데이터, 테스트 5개(version/help/subhelp/doctor-error/argparse).

### TUI (2026-10-16)
- curses 기반 대화형 TUI(`kakao tui`): 방 목록(안읽음 뱃지)+메시지창+입력줄. 백그라운드 워커 스레드가 Driver 소유→기기 접근 직렬화(poll+send+open을 순차 처리), curses 스레드는 렌더·입력만.
- 한글 폭 계산(east_asian_width), get_wch 유니코드 입력, keypad 방향키, Tab 포커스.
- **핵심 버그 2개 해결**: (1) `keypad(True)` 누락으로 방향키 미동작, (2) **워커의 adb subprocess가 TTY stdin 상속해 curses 키 입력 탈취** → adb.py 전 호출에 `stdin=DEVNULL`. tmux 자동화 시 Enter는 `C-m`.
- 탐색 견고화: open_room/open_chats_tab/open_self_chat/ensure_main 재시도 + 이물 foreground(파일선택기/다이얼로그) 복구 + force-stop 폴백. list_rooms는 비면 재시도. open_room 이름매칭을 text OR content-desc로(자기채팅 행은 name이 desc).
- 검증: tmux PTY에서 방 목록 로드→자기채팅 열기→메시지 표시→한글 입력→전송(디바이스 readback count=1, status "sent (verified)").

### TUI e2e 테스트 (2026-10-16)
- `tests/e2e_tui.sh [serial]`: tmux PTY에서 TUI 구동→방 로딩 대기→자기채팅 **행 위치 동적 탐색**(방 순서는 수신으로 바뀜)→한글 입력→전송→**디바이스 readback 독립 검증**→종료. 3/3 통과.
- 과정에서 발견·수정: 채팅목록이 **"Unread" 필터**일 때 부분(4개)만 보임 → open_chats_tab이 **"All" 칩 선택** 보장. list_rooms는 부분 렌더 대비 여러 번 읽어 최대 스냅샷 채택. (Enter는 tmux `C-m`.)

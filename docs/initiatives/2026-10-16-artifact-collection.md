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

### 로그아웃→재로그인 재검증 (2026-10-16)
- **로그아웃**: 서브기기 설정 UI에 로그아웃 버튼 없음 → `adb shell pm clear com.kakao.talk`로 세션/데이터 초기화(채팅은 로그인 후 서버 재동기화). 로그인 화면 복귀 확인.
- **태블릿 지문·IME 영속**: build.prop(컨테이너 prop)·ADBKeyBoard IME(시스템 설정)는 pm clear로 안 지워짐 → "Use with Main Device" 여전히 노출, 한글 입력 유지.
- **QR 재로그인 주의**: 스캔 후 태블릿에 뜨는 **보안코드(인증번호)를 폰에 입력**하는 단계가 있음. 코드는 금방 만료("The security code has expired") → **QR 자동 새로고침을 이 단계에 돌리면 코드가 재생성돼 어긋남**. 자동 새로고침 끄고, 스캔 후 번호를 즉시 입력해야 함. 번호는 라이브 screencap 페이지로 사용자가 봄.
- **온보딩 자동화**: Skip and Start→OK→권한 OK→백그라운드 ALLOW, pm grant로 권한 선부여.
- **재검증**: doctor OK / rooms 13 / memo 한글 전송 verified / TUI e2e PASS 모두 통과.

### 웹 UI (2026-10-16)
- `kakao webui`: TUI와 **동일한 톤앤매너**(모노스페이스·다크·역상 바, 좌측 방목록+안읽음 뱃지 / 우측 메시지창+"— room —" 타이틀 / "> " 입력줄 / 역상 상태바)를 브라우저로. 키맵도 동일(Tab 포커스, ↑↓/Enter 방선택, Enter 전송, Esc 목록, r 새로고침).
- **제3자 의존 0**: stdlib `http.server`(ThreadingHTTPServer)만 사용, HTML/CSS/JS는 PAGE 문자열로 메모리 서빙(CDN 없음). TUI의 `Worker`를 `worker.py`로 분리해 tui·webui가 공유 → 단일 워커가 Driver 소유·기기 접근 직렬화. HTTP 핸들러는 큐에 명령만 push, 브라우저는 1초마다 `/api/state?since=N` 폴링(워커가 2.5s마다 갱신, 204로 무변경 스킵).
- API: `GET /`, `GET /api/state`, `POST /api/open{room}`, `POST /api/send{text}`(방 미오픈=400), `POST /api/rooms`. JSON은 `ensure_ascii=False`+utf-8. 상태 전이는 TUI와 동일(open 시 뷰 비우고 opened 이벤트 전까지 room=None, 타 방 messages 이벤트는 드롭 → 이전 방 로그 깜빡임 방지).
- **보안**: 기본 `127.0.0.1` 바인드. `--host 0.0.0.0`은 인증 없이 계정 제어를 네트워크에 노출하므로 기동 시 경고 출력 + SSH 터널(`ssh -L`) 권장. v1은 송신/파일전송 미포함(TUI 패리티만). 후속: 파일 업로드→send-file 연동, 토큰 인증 옵션.
- **검증**: 단위 테스트(스텁 워커로 서버 부팅→페이지·JSON API·한글 send, 7/7 통과). **브라우저 e2e**(`tests/e2e_webui.sh`+`tests/webui_browser.js`, playwright-core+캐시 Chromium): VM 배포→webui 기동→헤드리스 크롬이 페이지 로드(13방)→자기채팅 행 클릭→한글 입력→Enter→상태바 "sent" 확인→**adb readback 독립 검증** PASS. 커밋 a3a600f.

### 웹 UI 파일 전송 (2026-10-16)
- `webui` 입력줄 왼쪽 **`+`**로 파일/이미지 첨부. 흐름: 브라우저 파일 선택 → `POST /api/sendfile?name=`(raw 바이트, Content-Length만큼 1MiB 청크로 서버 임시파일에 스트리밍—영상 메모리 폭발 방지) → 워커 `("sendfile", path)` → `driver.send_file(self.room, path)` 재사용 → 임시파일 unlink. 전송 후 입력창 없으면 방 재진입 후 폴링(공유 인텐트가 앱을 방 밖에 남길 수 있음).
- **파일명 새니타이즈**: `_safe_name`이 basename만 취하고 `[^\w.\-]`→`_`(파이썬 `\w`는 한글 보존), 확장자 유지(send_file가 확장자로 이미지/영상 vs 문서 라우팅). send_doc가 파일명을 `am broadcast -d file://` 및 DocumentsUI 검색에 그대로 넣으므로 공백·따옴표·경로구분자 제거 필수.
- **태블릿 2-pane 발견**: SM-X710 지문이라 자기채팅·일반방 **모두 MainActivity 안에서** 열림(별도 ChatRoomHolderActivity/MemoChatConnectActivity 아님) → 액티비티로 자기채팅 구분 불가. 다만 **공유 피커의 자기채팅 행 desc에 계정명이 포함**(`not selected, me, <계정명>, …`)되어 `send_file(방이름)`이 이미지·문서 모두 매칭 → 자기채팅 특수처리 불필요.
- **검증**: 단위 2개 추가(바이트 스트리밍+새니타이즈 디스크 기록, 경로탈출 basename화, `_safe_name` 한글 보존) 총 9/9. 브라우저 e2e(`webui_browser.js` FILE 모드 = setInputFiles 숨김 input): 텍스트·문서(zip, readback=파일명)·이미지(png, 공유 피커 경유 status "sent"+state `[Photo]`) 자기채팅 전송.
- **send_doc 잠복 버그 수정**(문서 전송 중): DocumentsUI 검색창(`search_src_text`)에 파일명을 타이핑하는데, 결과 선택 시 `name in text` 첫 매칭이 **검색창 자체**를 잡아 탭→미선택. 게다가 readback `name in dump`가 그 검색어를 보고 **오탐 "sent"**(실제 전송 안 됨). → 결과 행의 `rid=.../title` 노드를 선택하도록 고치고, readback을 **DocumentsUI 이탈(현재 액티비티가 다시 com.kakao.talk) 확인**으로 교체. 실제 File 말풍선(`…zip … Open File`) 생성 검증.
- **webui open/send 레이스**: 방 재클릭 시 `want_open`이 room을 잠깐 비우는 틈에 업로드 POST가 400 날 수 있음 → 서버 가드를 `has_target()`(room **또는** pending)으로 완화(명령 큐가 open→sendfile 순서 보장). e2e 파일모드는 open 완료를 기다린 뒤 업로드.
- v1 범위: 송신만. 수신 미디어 표시는 기존 `[Photo]`/`[Video]` 플레이스홀더(썸네일 렌더 아님) — 후속.

### 웹 UI 수신 미디어/파일 다운로드 (2026-10-16)
- 메시지 창의 사진·영상·파일 말풍선을 **클릭하면 브라우저로 내려받기**. chatId·DB 복호화 없이, **열린 방의 보이는 말풍선만** 다루어 프라이버시 안전(크로스룸 유출 불가).
- **export-by-tap + before/after diff**: 서버(워커)가 해당 말풍선 bounds를 탭→뷰어의 **Save** 탭→KakaoTalk 저장 디렉터리 diff로 새 파일 포착→pull. 저장 위치 실측: 사진=`/sdcard/Pictures/KakaoTalk/<ts>.png`, 문서=`/sdcard/Download/KakaoTalk/<원본명>.<ext>`(logcat의 MediaProvider 이동 로그로 확인). 탭 후 `ensure_main`+`_open_target`로 방 재진입.
- **말풍선 분류**(`_messages_detailed`): chat_log 행 leaf rid로 kind 판정 — 파일=`txt_size`(+`bubble`,`message`=파일명), 사진=`image`, 영상=`video`, 그 외 텍스트. 각 미디어/파일에 탭용 bounds 포함. `_messages_on_screen`은 text만 뽑는 얇은 래퍼로 유지(poll/read/TUI 무변경, TUI는 dict→text 매핑).
- **2-pane 이점**: 방을 열어도 좌측 방목록과 우측 메시지가 한 dump에 공존(앞서 확인). 미디어 node bounds == screencap 좌표계(검증)라 향후 썸네일 인라인도 가능(이번엔 다운로드만).
- **API**: `POST /api/export{kind,rank,name}`→202 `{id}`; `GET /api/export/<id>`→`{ready,token|error}`; `GET /api/download/<token>`→파일 스트림(`Content-Disposition: attachment; filename*=UTF-8''…`, mime는 확장자 매핑). 워커가 export_dir(webui tmpdir)로 pull. 같은 `(kind,name)`은 이름, 사진/영상은 same-kind 내 rank로 fresh dump에서 재매칭(폴 사이 목록 변동 대비).
- **검증**: 단위 10개(스텁 워커 export→download 바이트 일치 포함). 브라우저 e2e(`webui_recv.js`, playwright download 이벤트 캡처): 자기채팅에서 송신한 zip·png를 클릭→다운로드→**유효 zip/PNG 바이트 확인**. 전체 e2e(송신 text/문서/이미지 + 수신 문서/이미지) PASS, TUI e2e 회귀 PASS.
- 후속: 영상 수신(사진과 동일 경로 가정, 실검증 대기), 썸네일 인라인 표시(screencap 크롭), 만료된 말풍선(Expiry 경과) 처리.

### 웹 UI 모바일 웹 지원 (2026-10-17)
- **반응형 단일화면 전환**: 좁은 화면(≤720px, `@media`)은 방 목록 ↔ 대화 화면으로 전환(`body[data-view]`). 방 탭→대화(`history.pushState`), 타이틀 바 **[←]** 또는 **브라우저 뒤로가기**(`popstate`)로 목록 복귀. 데스크톱은 기존 2-pane 유지. 키보드 힌트 바는 모바일에서 숨김.
- **터치**: 행·버튼 44px, 입력줄에 **[보내기]** 버튼(키보드 Enter와 병행), `+` 터치 크기 확대, `⤓` 받기 버블은 `<button>`으로 탭 가능+진행표시(`dl-busy`). `@media (hover:none)`에서 `:active` 피드백.
- **뷰포트/키보드**: `height:100dvh`(+`100vh` fallback), `env(safe-area-inset-*)` 반영(viewport-fit=cover), `visualViewport` resize로 body 높이 보정해 가상 키보드 올라와도 입력줄 노출. 입력 `font-size:16px`로 iOS 확대 방지(`maximum-scale`은 쓰지 않음).
- **스크롤 보존**: 메시지는 기기의 *보이는 창*이라 새 메시지 도착 시 창이 이동(순수 append 아님). 정책 단순화 — 바닥에 있었으면 따라가고(`wasBottom`), 위로 올려둔 상태면 `scrollTop` 복원 + **"새 메시지 ↓"** pill. `replaceChildren`가 scrollTop을 0으로 리셋하므로 갱신 전 위치를 저장해 복원. 데스크톱도 동일.
- **렌더 최적화**: 서버 `State.apply`가 rooms/status/messages **실제 변경 시에만 seq 증가**(워커가 2.5s마다 동일 내용 재emit → 매번 bump하면 DOM 재생성·깜빡임·입력 포커스 상실). 클라이언트 `render()`는 rooms/선택/메시지/상태/타이틀을 각각 diff해 바뀐 부분만 갱신, 입력 요소는 재생성하지 않아 포커스 유지.
- **접근성**: 메시지 영역 `role=log`, 방 목록 `role=listbox`+`aria-selected`, 상태바 `role=status`, 미디어 행 `<button>`.
- **금지 준수**: 제3자 의존·CDN 없음(인라인 PAGE), 기본 바인드 127.0.0.1·0.0.0.0 경고 유지, `maximum-scale` 미사용. 폰 접속은 SSH 터널/Tailscale 권장만 README에 안내.
- **검증**: 단위 10/10. 브라우저 e2e(playwright 기기 에뮬 iPhone-13 viewport+hasTouch+isMobile): 목록→방 탭→대화 전환(list display:none 확인)→한글 입력→**[보내기] 탭**→status sent→adb readback→**[← 목록]**→목록 복귀. 데스크톱 송신/수신 회귀 + 스크롤 보존 테스트(`webui_scroll.js`: 위로 올린 뒤 새 메시지 도착 시 top 유지+pill) 통과. 스크린샷 3장(아래).
- 스크린샷: `/tmp/webui_desktop.png`, `/tmp/webui_mobile_list.png`, `/tmp/webui_mobile_convo.png`.

### 웹UI 메신저 개선 — B0 화면 구조 조사 (2026-10-17, 읽기 전용)
자기채팅·1:1(idx4)·그룹(idx1) 라이브 `uiautomator dump` 조사. 원본 덤프는 로컬 `/tmp/b0_*.xml`(미커밋), 값은 마스킹. 화면폭 1600px.

**B0 필드 표** (chat_log_recycler_list 행 기준):

| 필드 | resource-id / 판정 | 값 형식 | 확정 |
|---|---|---|---|
| 본문 | `message` | 텍스트 | ✓ |
| 발신자 | `nickname` (TextView) | 상대 표시명. **연속 메시지는 생략** → 직전 발신자 상속 | ✓ |
| 프로필 | `profile` (아바타, content-desc) | 이름 포함 가능하나 `nickname`이 깔끔 | ✓(보조) |
| is_mine | **기하**: `message_container` 우측모서리 x2≈1569(우측여백 밀착, 비율>0.93)=내 것 / 수신은 좌측열 x1≈840 고정 | nickname 유무만으론 연속수신과 구분 불가 → 기하 필요 | ✓(휴리스틱) |
| 날짜 구분행 | `time_line` (별도 row) | 날짜 문자열(예: "YYYY년 M월 D일 요일") | ✓ |
| 인용답장 | `source_layout`/`source_nickname_prefix`/`source_nickname`/`source_message` | 답장 대상 원문(발신자 아님 주의) | ✓ |
| 파일 | `txt_size`(+`bubble`,`message`=파일명) | (기존) | ✓ |
| 사진/영상 | `image`/`video` | (기존) | ✓ |
| **시각** | — | `chat_info` 노드는 있으나 **텍스트 비어있음**(커스텀 드로잉) → uiautomator 미노출 | ✗ 표시안함 |
| **읽음수("1")** | — | 메시지 주변 standalone 숫자 노드 없음(members_count/toolbar count만 존재) | ✗ 표시안함 |
| **시스템 메시지** | — | 가시 창에서 미관측, 전용 rid 미확정 | ✗ 표시안함 |

- **연속 생략 메커니즘**: 같은 발신자의 첫 메시지만 `profile`+`nickname`, 이후 동일 발신자는 `message`(+`chat_info`)만. 1:1·그룹 구조 동일.
- **결론(B 범위)**: `sender`·`is_mine`·날짜행(`time_line`)만 추가 추출. 시각·읽음수·시스템행은 미확정이라 **표시하지 않음**(추측 금지 원칙).

### 웹UI 메신저 개선 — A 화면만 (2026-10-17, 새 데이터 없음)
state의 기존 방 데이터(last_message/time/members_count)만으로 프런트 개선. driver/서버 무변경.
- **방 목록 2줄 행**: `.r1`(이름+인원+시각 우측) / `.r2`(dim 미리보기 + unread 배지 우측). 모바일 `.row`는 `flex-direction:column`.
- **입력 textarea**: 자동 높이, Enter 전송·Shift+Enter(및 Ctrl+Enter 수동삽입) 줄바꿈, `enterkeyhint=send`, 방별 초안 `localStorage(kakao.draft:<room>)` 보존(방 떠날 때 저장·열 때 로드·전송 시 삭제). 이미지 붙여넣기(paste)·드래그앤드롭 → 기존 `/api/sendfile` 재사용(`uploadFile`).
- **빠른 전환**: `/` 또는 Ctrl+K 오버레이, 방이름 subsequence 퍼지 필터, ↑↓/Enter/Esc.
- **탐색 키**: Alt+↑/↓ 이전·다음 방, Alt+Shift+↑/↓ 안읽음 방만(입력 중에도 동작). `?` 도움말 오버레이. 단일 키(r/m/n/ /?)는 입력 포커스 중 미발동(keydown에서 `e.target===input` 분기).
- **알림/배지**: `document.title="(N) kakao"`(조용한 방 제외 합계). `m` 현재 방 조용히 토글(`kakao.quiet`), `n` opt-in 알림 토글(권한은 토글 시 요청, `document.hidden`일 때만 + 조용한 방 제외). 방 행에 조용히 `●` 표시.
- **접근성**: 행 `data-room`+`role=option`+`aria-selected`, 메시지 `role=log`, 상태 `role=status`, 미디어 `<button>`(기존).
- **금지 준수**: 제3자·CDN 없음, 말풍선 기본값/`maximum-scale` 미변경, 바인드 기본값 불변.
- **검증**: 단위 10/10. 브라우저 e2e(`webui_features.js`): 2줄 행 구조 확인 → `/` 퀵스위처로 자기채팅 열기 → Shift+Enter 줄바꿈(`line1\nline2`) 확인 → Enter 전송 → adb readback. 데스크톱/모바일/스크롤/송수신 전체 e2e PASS. 스크린샷 갱신(`/home/agent/kakao-tasks/shots/`).

### 웹UI 기기 화면 실시간 보기·조작 (C, 2026-10-17, 로그인용)
웹 UI에서 태블릿 화면을 보고 조작해 재로그인(QR/보안코드/온보딩)·막힌 팝업 해제를 끝낼 수 있게. **실제 로그아웃/재로그인은 하지 않음**(로그인 감지는 fixture 단위테스트).
- **캡처**: `adb exec-out screencap -p`(PNG, 메모리만, 디스크 미저장). `screen.py`의 `ScreenBuffer`가 최신 1프레임+seq 보관, **viewer TTL(2s)** — 프레임/`info` 요청이 2초 내 있을 때만 `active()`. 워커는 `screen.active()`면 캡처 루프(간격 0.25s+screencap)로 전환하고 **채팅 폴링 중지**(adb 경합 방지). `set_frame`은 **동일 바이트면 seq 미증가**(대역폭 절약). 클라이언트는 `/api/screen/info`(작음, viewer 터치)로 seq 확인→**변할 때만** `<img>`로 프레임 수신. **실측 fps 1~3**(정적 화면은 더 낮음), 프레임 1600×2560 PNG ~0.4–0.6MB.
- **조작**(Worker 큐 직렬화): `POST /api/screen/tap|swipe|key|text`. 좌표변환 `view_to_device`(레터박스/스케일/clamp; screencap이 현재 회전으로 나와 `input`과 동일 좌표계라 회전변환 불필요). 텍스트는 ADBKeyBoard 재사용(한글). key=back/home/enter.
- **로그인 감지**: `is_login_screen(activity,xml)` — 액티비티(Authenticator 등) 또는 로그인 rid(signup_kakao_account_with_qr/chk_subdevice/...). 워커 `_maybe_login`(2s 스로틀)이 상태 변화 시 `login` 이벤트 emit→상태바/배너 "로그인이 필요합니다 — [화면 열기]". 로그인 완료 시 방목록 자동 재로드. 방목록 실패를 반복 에러로 쏟지 않음.
- **보안**: 화면 API는 전체 계정 원격조작 → 기본 바인드 127.0.0.1 유지. **비로컬 `--host`면 기본 OFF**, `--allow-screen` 명시해야 ON(기동 경고). 조작 POST는 JSON Content-Type만 + Origin이 있으면 Host 일치 검사(CSRF). 프레임 디스크 미저장.
- **UI**: 타이틀·상태바의 `⧉ 화면`(모바일 로그인처럼 방이 없어도 상태바 버튼은 항상 보임)·`g` 키·로그인 배너로 오버레이 열기. `<img touch-action:none>`에 pointerdown/up으로 탭(이동<12px)·스와이프, Back/Home/Enter 버튼, 한글 텍스트 입력. 배너는 `.banner[hidden]{display:none}`로 숨김(= `.banner{display:flex}`가 `[hidden]` UA 스타일을 덮어쓰는 버그 수정).
- **검증**: 단위 15/15(좌표변환 레터박스/clamp, 로그인감지 fixture, png_size, ScreenBuffer active, 비활성 403, 조작 입력검증/CSRF 415). e2e(`e2e_screen.sh`+`webui_screen_check.py`+`webui_screen.js`): 프레임 부트스트랩·**탭으로 Chats↔Friends 전환 dump 검증**·프레임 변화·fps 실측·유휴 시 캡처중지·오버레이(데스크톱/모바일)·`--allow-screen` 없는 비로컬 바인드 403. 스크린샷 `/home/agent/kakao-tasks/shots/screen_{desktop,mobile}.png`.

### 웹UI 메신저 개선 — B 데이터 확장 (2026-10-17, B0 확정필드만)
B0에서 확정한 필드만 `driver._messages_detailed`에 추가(하위호환: 필드 추가만, TUI/poll은 text 래퍼라 무영향).
- **추출**: `sender`=`nickname`(연속 메시지는 앱이 생략 → 빈 문자열, 프런트가 상속). `is_mine`=기하(행 bounds는 full-width라 `message_container`(없으면 `bubble`) 우측모서리 x2 ≥ 화면폭×0.93이면 내 메시지; 수신은 좌측열 고정). 날짜행 `time_line`→`{kind:"date"}`. 인용답장 `source_*`는 발신자 아님(무시).
- **표시 안 함(추측 금지)**: 시각·읽음수(uiautomator 미노출, `chat_info` 비어있음), 시스템행(미확정).
- **프런트(IRC형)**: `보낸사람 │ 본문` 한 줄(flex, 이름 열 8ch 우정렬·ellipsis, hanging indent). 연속 동일 발신자는 이름 `·` 생략(클라가 prevKey로 판정, 날짜행에서 리셋). 내 메시지는 `나`+`--me` 색. 발신자 이름은 해시 6색(WeeChat식). 날짜 구분선 `──── <날짜> ────`는 `position:sticky`로 스크롤 중 상단 고정. 미디어/파일은 같은 줄의 `⤓` 버튼(`.dlbtn`)으로 유지.
- **검증**: 단위 15/15. 자기채팅(100% 내 메시지)→전부 `나`/`·`로 is_mine 정확 확인. 그룹방 구조(마스킹 카운트): 내 메시지 우측·상대 이름+색·연속 `·`. 전체 webui e2e(수신 셀렉터 `.dlbtn`로 갱신, 송수신/모바일/스크롤/A기능) PASS. 스크린샷 갱신.

### webui 버그 수정 — 한글 IME 마지막 글자 중복 전송 (2026-10-19)
증상: 웹UI textarea에서 한글 메시지 Enter 전송 시 **마지막 글자가 별도 메시지로 한 번 더** 전송(`아아 테스트`→`트`). 원인: 마지막 음절이 **조합 중(isComposing)** 일 때 Enter가 먼저 `sendCurrent()`를 호출→전체 전송 후 조합이 끝나며 남은 글자가 빈 입력창에 들어가 다음 Enter로 재전송. `send_in_room`(driver)은 정상(직접 호출 중복 없음) — 순수 프런트 IME 처리 버그. 수정: 입력창·화면텍스트 Enter 핸들러에 `!e.isComposing && e.keyCode!==229` 가드. 검증: `tests/webui_ime.js`(조합 중 Enter=전송0, 조합 후 Enter=전체1회) + 정상 전송 device-verified. e2e에 스텝 추가.

### 웹UI 콘솔 모드 — 기기 화면 + 듀얼 파일매니저 (2026-10-20)
요청: 중앙 안드로이드 화면 스트리밍 + 클릭/키보드 직접 조작, 좌측 상하 분할(위 안드로이드 내부저장소, 아래 로컬 저장소) + 파일 이동.
- **기본값(결정)**: 기존 채팅 UI를 지우지 않고 **두 번째 모드**로 추가(상단 `[채팅|콘솔]`, `c`, `#console`, 마지막 모드 localStorage; 기본=채팅 → 기존 e2e 무영향). **"로컬" = webui 호스트(VM) 디스크**(브라우저는 노트북 FS 접근 불가; 브라우저↔호스트 업/다운로드는 후속). **복사만**(adb pull/push; 이동·삭제·덮어쓰기 없음, 충돌 시 `-N` 접미사, 폴더 미지원). 로컬 패널은 `--fs-root`(기본 `~`) realpath 격리. 콘솔 전체는 화면 API와 같은 게이트(로컬 바인드 or `--allow-screen`) + JSON/same-origin.
- **백엔드**: `fs.py`(순수 헬퍼: `shq` 단일따옴표 쿼팅, `%F|%s|%Y|%n` stat 파서(이름에 `|` 허용, 경로가 마지막 필드), `LocalRoot` 격리, `unique_name`). 안드로이드 목록은 `find '<dir>/' -maxdepth 1 -mindepth 1 -exec stat -c … {} +`(끝 `/`로 `/sdcard` 심볼릭링크 내용 나열; redroid adbd=root라 `/data`도 보임; 권한 오류는 패널에 표시). 목록은 핸들러 스레드에서 adb 직접 호출(adb 서버가 transport 직렬화, UI 상태 무관). pull/push는 **백그라운드 스레드 + job id**(`/api/fs/job/<id>`) — 워커 큐로 보내면 큰 전송 동안 캡처·탭이 멈춤. 확장자 없는 콘텐츠 블롭은 pull 후 `media._sniff`로 확장자 부여. push 후 MEDIA_SCANNER 브로드캐스트.
- **키보드 패스스루**: `/api/screen/key`가 허용목록 정수 키코드 수용(DEL67·ENTER66·TAB61·방향19–22·FWD_DEL112·MOVE_HOME/END122/123·PGUP/DN92/93·HOME3·BACK4), 그 외 400. 프런트는 숨은 `<textarea id=kbcap>`로 캡처(편집 가능한 요소여야 IME 조합 이벤트가 옴) — `isComposing`/229 동안 무시, `compositionend`/조합 아닌 `input`에서 값 취득(둘 중 먼저 오는 쪽이 비우므로 중복 없음), ~150ms 묶어 `/api/screen/text` 1회. 화면 클릭 시 kbcap 포커스(+기기 탭), Esc 해제, 포커스 링 표시.
- **화면 요소 1개**: `<img id=scr>`를 오버레이(로그인용)와 콘솔 중앙 사이에서 **재배치**, 프레임 루프도 1개(`screenWanted()` = 오버레이 표시 or 콘솔 모드) — 중복 프레임 요청 없음.
- **잠복 버그 수정(driver)**: `nodes.get("message_container") or nodes.get("bubble")` — ElementTree 요소는 자식이 없으면 falsy라 잘못 대체될 수 있음(DeprecationWarning로 발견) → `is not None` 명시.
- **e2e 안전장치 교훈**: 첫 콘솔 e2e에서 자기채팅이 채팅목록 화면 밖이라 `open_room(이름)` 실패 → 기기에 열려 있던 **실제 단체방 입력창에 테스트 문자열 입력**(전송 X, ADB_CLEAR_TEXT로 정리·잔여 초안 없음 확인). 하네스를 `me`(친구탭→My Chatroom, 목록 위치 무관)로 열고 **보이는 메시지가 전부 is_mine일 때만 타이핑**하도록 수정. (`open_room`은 보이는 행만 찾음 — 목록 스크롤 탐색은 후속.)
- **검증**: 단위 21/21(파서·쿼팅·루트 격리(`../`·외부 심볼릭링크)·덮어쓰기 방지·키코드 허용목록·비활성 403·가짜 기기로 목록/pull/push 작업). `e2e_console.sh`+`webui_console.js`: `c`로 콘솔 전환·중앙 스트림·`/sdcard/Download` 탐색·push(기기 `cat` 내용 일치)·pull(`-1` 이름, 원본 불변, 디스크 확인)·자기채팅 확인 후 화면 클릭→한글 조합 입력이 기기 입력창에 들어감(dump 확인, 미전송, 정리). 회귀: `e2e_screen.sh`(오버레이 재배치) PASS, `e2e_webui.sh`(채팅 모드). 스크린샷 `/home/agent/kakao-tasks/shots/console_desktop.png`.
- **하네스 보강**: 채팅 모드 e2e가 "자기채팅 행 없음"으로 실패(실대화가 쌓여 자기채팅이 채팅목록 화면 밖 — 환경 드리프트, 콘솔 변경과 무관). `e2e_webui.sh`에 시작 전 `memo`(자기채팅)로 목록 맨 위로 올리는 1b 단계 추가 → 전체 PASS. 배포 목록에 `screen.py`·`fs.py`(+screen e2e에 `driver.py`) 추가.

### 콘솔2 — 전송·클립보드·비디오 (2026-10-20, TASK_console_transfer_clip_video.md)
**실측(착수 전)**: scrcpy-server v5.0.1 공식 자산 sha256 `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`(733,930B). redroid에서 **H.264 동작**(SW 인코더), max_size 1280→800×1280, SPS `42 C0 29`(→`avc1.42C029`), 연결→첫 프레임 0.07s, **RESET_VIDEO(17)→키프레임 0.1s**, 정적 위주 8s 평균 5.6fps/575kbps(변화 시에만 프레임). **v5 스트림 프로토콜**: 코덱ID(4B) 뒤 12B 헤더 — 최상위 비트=세션 패킷(flags,w,h, 페이로드 없음), 아니면 `pts|flags(u64)`(bit62 config, bit61 key)+`size(u32)`+페이로드. 옵션명 `send_stream_meta`(구 send_codec_meta 아님). 제어: 키 14B, 터치 32B(위치는 **비디오 크기 좌표**+sw/sh 일치 필수), SET_CLIPBOARD `[9][seq][paste][len][utf8]`, SCAN_FILE(22), START_APP(16), RESIZE_DISPLAY(21). scrcpy 터치 주입으로 Friends 탭 선택 확인. adb forward는 서버 기동 전에도 연결을 받고 끊으므로 첫 소켓은 더미 바이트까지 재시도.

**결정(기본값)**: ① 순서 조정 — A → **W**(사용자 추가 결정: 웹 채팅 제거, M 대체) → B → **V+V2**(비디오를 처음부터 가상 디스플레이+flex로, 미러 폴백). W를 먼저 해 곧 지울 채팅 UI/e2e 위에 B·V를 쌓지 않음. ② A-1·A-2를 한 단계로 구현·커밋. ③ Host 허용목록 = IP 리터럴 + localhost + `--allow-host`(DNS rebinding은 공격자 호스트명이 필요 → IP 리터럴은 안전; 0.0.0.0 바인드에서도 인터페이스 열거 불필요). ④ 업로드 파일명은 원본 유지(한글·공백), 경로부분·제어문자만 제거. ⑤ 업로드 Content-Type: 브라우저는 항상 `application/octet-stream`으로 보내고 서버는 multipart/urlencoded(폼 전송형)만 거절 — `text/plain` 거절은 .txt 업로드를 깨뜨리므로 안 함, 진짜 CSRF 방어는 `X-Kakao-Op`(커스텀 헤더 → preflight 강제, OPTIONS는 허용하지 않음=501). ⑥ 이름 충돌·경쟁: `.up-*.part`(mkstemp) → `os.link`로 빈 이름에만 배치(존재하면 FileExistsError → 다음 `-N`) → part 삭제 = 원자적 no-clobber. ⑦ Range(이어받기) 1차 제외(후속).

**A (A-1+A-2) — 브라우저 ⇄ VM/기기 파일**: `POST /api/fs/upload?dir=&name=[&target=android]`(raw body 1MiB 청크, 크기 `--max-upload` 413, 여유공간−64MiB 507, Content-Length 없으면 411, 중단 시 part 삭제), 안드로이드는 tmpdir 스테이징→기존 push 체인(+미디어 스캔)→스테이징 삭제. `GET /api/fs/local/get`(청크 스트리밍, `application/octet-stream`, `nosniff`, `filename*=UTF-8''`), `GET /api/fs/android/get`(stat 크기 → `adb exec-out "cat '<path>'"` 직접 스트리밍; 경로는 단일따옴표 쿼팅). 모든 콘솔 GET/POST에 screen 게이트+Host 허용목록. 기존 `_serve_file`도 스트리밍+nosniff로(내보내기 다운로드 공용). UI: 패널별 `⇪ 올리기`·드래그앤드롭, XHR `upload.onprogress`→작업 줄 `%`, 파일 행 `⤓`/더블클릭 다운로드.
- 검증: 단위 25/25(Host 허용목록, 파일명 정리, 한글 Content-Disposition, 업로드 성공·`-1` no-clobber·part 잔존 없음, 헤더 누락/Origin/Host/multipart/413/507/루트 밖/`..` 거절, 중단 업로드 정리, 안드로이드 스테이징 push·스테이징 삭제, 기기 스트리밍 다운로드·404·400). e2e(`e2e_console.sh` 6단계, `webui_transfer.js`): 6MB 업로드(진행률 관측)→VM sha256 일치, VM→브라우저 sha256, 1.5MB 안드로이드 패널 드롭→기기 `sha256sum` 일치, 기기→브라우저 sha256, `.part` 잔존 없음, 모바일 세로 화면 왕복 sha256.

**W — 웹UI 콘솔 전용(사용자 추가 결정, M 대체)**: 웹에서 채팅 모드를 제거 — 모드 토글·`#chat`·`c` 키·방목록/메시지/입력/퀵스위처/도움말(채팅키)·로그인 배너·화면 오버레이와 웹 전용 API(`/api/open`·`/api/send`·`/api/sendfile`·`/api/export*`·`/api/download/*`·`/api/rooms`, 채팅용 `/api/state` 필드)를 삭제. 페이지는 콘솔만(헤더=도움말, 중앙 화면, 좌측 파일패널, 상태바=상태+전달방식 배지). `/api/state`는 `{seq,status}`만(워커 오류 표시용, 변경 시에만 seq↑).
- **공용 코드 판정(grep)**: TUI는 워커의 `rooms/open/send/quit`만 사용 → 워커의 `export/sendfile`·`_mime_for`·`last_detailed`는 웹 전용이라 삭제. driver 채팅 기능(`_messages_detailed`·`export_visible`·`send_file` 등)과 `_maybe_login`(TUI 경로)·`is_login_screen`은 **유지**(`export_visible`은 현재 호출처 없음 — CLI용으로 남김).
- **웹 워커**: `Worker(chat=False)` — 시작 시 방목록 로드(채팅 탭으로 화면을 바꿔버림)·로그인 감시·채팅 폴링을 하지 않음. 콘솔 입력(adb)·PNG 캡처·ADBKeyBoard만. 이유: 콘솔만 남으면 채팅 상태가 쓸 곳이 없고, 시작 시 탭 이동은 사용자가 보고 있는 화면을 건드림.
- **테스트**: 채팅 웹 e2e 삭제(`webui_browser/features/recv/scroll.js`, `e2e_webui.sh`)·채팅 웹 단위 테스트 삭제(페이지/전송/sendfile/_safe_name/export). 콘솔 기준으로 전환: IME 회귀(`webui_ime.js` — kbcap에서 조합 중 텍스트·Enter 키코드 0건, compositionend 후 1회), `webui_screen.js`(콘솔 중앙 프레임, 데스크톱/모바일), `webui_shot.js`(콘솔 스크린샷), 신규 단위 `test_webui_console_page_and_state`(채팅 엔드포인트 404, state 키 2개). 콘솔 e2e의 안전확인(자기채팅·전부 is_mine)은 웹 API 대신 **VM에서 driver 직접 호출**로. 결과: 단위 22/22, `e2e_console.sh`(IME 포함)·`e2e_screen.sh`·`e2e_tui.sh` PASS. 스크린샷 `shots/console_only_{desktop,mobile}.png`.

**B — 클립보드(scrcpy control)**: `scrcpy.py` 신설 — jar 확보(`ensure_server`: `--scrcpy-server` 경로 또는 `~/.cache/kakao-cli/` 캐시, 없으면 공식 릴리스에서 1회 다운로드, **sha256 불일치 거부·`.part` 삭제**), 순수 프로토콜(인코더: 키14B/터치32B/SET_CLIPBOARD/RESET_VIDEO/START_APP/RESIZE_DISPLAY/SCAN_FILE, 디코더: 기기메시지·v5 비디오 스트림 증분 파서, `avc_codec_string`), `ScrcpySession`(서버 1개 상주, 감독 스레드가 백오프 1→30s 재연결, 제어 소켓 쓰기는 락 1개, 클립보드는 메모리 최신 1개). webui: 콘솔이 켜졌을 때만 브리지 기동, `/api/state`에 `clip_seq`·`scrcpy`(텍스트는 안 실음) → 바뀌면 `GET /api/clip`. `POST /api/clip {text,paste}`(JSON·same-origin·Host 게이트, 256KiB 한도). scrcpy 없으면 paste는 ADBKeyBoard 타이핑 폴백, 기기→브라우저 없음(503).
- **결정(기본값)**: 터치는 scrcpy 손가락 포인터(-2, 버튼 0) — 마우스 포인터(-1)는 버튼 필요. 서버 `cleanup=false`(true면 서버가 종료 시 jar를 스스로 지워 재시작이 깨짐) + 매 연결 시 jar push(≈730KB, 빠름) + 종료 시 우리가 rm. 서버 로그는 `log_level=warn`·출력 폐기(클립보드가 로그로 새지 않게). 브라우저 자동 복사는 보안 컨텍스트에서만 노출, 기본 OFF.
- **프런트**: 기기 복사→토스트("기기 클립보드: 미리보기 [복사]", http는 `execCommand` 폴백), 화면 포커스 중 Ctrl/Cmd+V: 텍스트→SET_CLIPBOARD(paste=1), 이미지/파일→`/sdcard/Download` 업로드(+미디어 스캔), `📋 기기로` 버튼(보안 컨텍스트면 `readText`, 아니면 Ctrl+V 안내). 상태바 `📋 연동/—`.
- **정리 버그 수정**: tmux 종료(SIGHUP)는 SIGTERM이 아니어서 `bridge.stop()`이 안 불려 기기에 jar·forward가 남았음 → SIGTERM·SIGHUP 모두 정리 경로로, `stop()`은 감독 스레드 join 후 한 번 더 닫음. (착수 전 프로브가 예외로 죽어 남긴 서버 프로세스도 이때 발견·정리 — `pkill -f` 패턴이 ssh 명령줄 자신을 매칭해 중간에 끊긴 실수도 기록.)
- **검증**: 단위 27/27(인코더 바이트 레이아웃, 1바이트씩 쪼갠 수신에도 파싱, 비디오 세션/config/key/delta, `avc1.42C029`, sha256 고정: 정상·변조·없는 파일·다운로드 변조 시 캐시에 아무것도 안 남김, 재연결: 첫 연결 실패→백오프 후 성공→끊김→재연결, `/api/clip`: 기기 복사 시 seq↑·폴링에 본문 없음·paste·빈값/초과/텍스트 Content-Type 거절·scrcpy 없을 때 폴백/503). e2e 7단계: 원래 클립보드를 `clip_tool.py hold`로 **메모리에만** 보관 → 자기채팅(전부 is_mine) 확인 → 기기에서 최신 메시지 길게눌러 Copy → 브라우저 토스트 텍스트 일치 → 화면 클릭 후 Ctrl+V 텍스트가 기기 입력창에 들어감(dump, 미전송) → 지우고 비었는지 확인 → 이미지 붙여넣기 → 기기 `sha256sum` 일치 후 삭제 → 클립보드 복원(해시 일치). 8단계: SIGHUP 종료 후 forward·프로세스·jar 0.

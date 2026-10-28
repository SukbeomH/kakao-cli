# android-console 독립 모듈

`packages/android-console`은 KakaoTalk에 의존하지 않는 별도 Python 배포 패키지입니다.
외부 `adb`와 Python 3.11 이상이 필요하며 Python 런타임 의존성은 없습니다.
`kakao-cli`는 이 패키지를 의존성으로 사용합니다. 저장소에서는 uv workspace가 두 패키지를
함께 설치하고 하나의 uv.lock으로 관리합니다.

## 개발 설치와 기존 명령

```sh
uv sync --extra dev
uv run kakao webui
uv run kakao tui
# pip로 개발 설치할 경우 두 로컬 패키지를 함께 지정
pip install -e ./packages/android-console -e .
```

기존 `kakao webui`는 포트 8800, KakaoTalk 가상 화면, 숨긴 Android 시스템 내비게이션,
Home 없는 하단 버튼, 기존 캐시·브라우저 설정을 유지합니다.
CLI/TUI의 KakaoTalk 화면 자동화·DB 조회·로그인 감지는 `kakao_cli`에 남아 있습니다.
기존 `kakao_cli.adb/fs/fmp4/screen/scrcpy/video/webui/worker` import 경로도 유지됩니다.

## 기존 ADB 기기에 독립 연결

```sh
uv run android-console serve --serial DEVICE_SERIAL
uv run android-console serve --connect HOST:5555 --serial HOST:5555
# http://127.0.0.1:8801
```

기본값은 display 0 미러 모드입니다. 특정 앱을 자동 실행하지 않고 Home·최근 앱 버튼을 제공합니다.
ADB 기기가 여럿이면 `--serial`을 명시해야 합니다. `--connect`만 지정하면 해당 주소를 선택합니다.
실제 휴대전화와 VM이 함께 연결되어 있어도 다른 기기를 암묵적으로 선택하지 않습니다.

```sh
# 브라우저 크기에 맞는 별도 가상 화면에서 원하는 앱 실행
uv run android-console serve --serial DEVICE_SERIAL \
  --video-mode virtual --app com.android.settings
# PNG 폴백 / 오프라인 scrcpy-server 파일 / 공유할 로컬 디렉터리
uv run android-console serve --serial DEVICE_SERIAL --no-scrcpy --fs-root ./shared
uv run android-console serve --serial DEVICE_SERIAL --scrcpy-server /path/to/scrcpy-server-v5.0.1
```

scrcpy-server는 기존과 동일한 고정 버전·SHA-256을 사용합니다.
일반 콘솔은 `~/.cache/android-console`, Kakao 콘솔은 `~/.cache/kakao-cli`를 사용합니다.
기기의 서버 JAR 경로는 세션마다 달라 종료할 때 다른 콘솔의 파일을 지우지 않습니다.
가상 화면은 기기 측 소유자 잠금으로 보호합니다. 이전 kakao-cli가 읽는 잠금 파일 경로를
유지하며, 미러 콘솔이나 종료된 이전 세션이 다른 소유자의 잠금을 지우지 않습니다.

인증 없는 원격 기기 제어입니다. 기본 loopback과 SSH 터널을 권장합니다.
비로컬 바인딩의 화면 제어는 `--allow-screen`을 지정해야 켜집니다.
한글 등 Unicode 글자 입력은 기기의 ADBKeyBoard IME 설치·선택이 필요합니다.

### 키보드 반응성

조합이 끝난 글자는 대기 타이머 없이 즉시 전송합니다. 입력 요청은 하나씩 완료를 확인하고,
전송 중에 쌓인 연속 글자만 합쳐 느린 연결에서 요청이 밀리는 것을 줄입니다.
Enter·Backspace·붙여넣기의 순서와 한글 IME 조합을 유지합니다. 일반 타이핑은 클립보드를
바꾸지 않습니다. 전송 실패 시에는 적용 여부가 불확실한 글자나 대기 중인 Enter를 자동 재전송하지
않으므로 기기 내용을 확인한 뒤 다시 입력하세요.

PNG 캡처는 별도 읽기 스레드에서 실행해 화면 캡처가 키 입력 완료를 막지 않도록 합니다.
공통 구현이므로 `kakao webui`와 `android-console serve`에 함께 적용됩니다.
실측 조건과 전후 결과는 [키보드 반응성 검증 기록](verification/2026-10-26-keyboard-responsiveness.md)을 참고하세요.

한글의 마지막 음절이 다음 글자 입력까지 기다리던 문제는 **실시간 조합 전달**로 처리합니다.
`ㅎ → 하 → 한`을 Android 입력 칸의 같은 조합 영역에서 갱신하고, 조합 종료 때 한 번 확정합니다.
조합 중 Enter는 확정에만 쓰이며, 이후 Enter부터 기기로 전달합니다.
기기 입력 칸을 바꾼 뒤 도착한 이전 조합 요청은 거부합니다.

첫 조합 입력 때 패키지에 포함된 MIT 입력 보조 앱 `Android Console Input`을 해시 확인 후
선택한 ADB 기기에 설치·활성화합니다. ADBKeyBoard 또는 이 보조 앱이 이미 선택된 기기에서만
전환하며, 사용자가 선택한 다른 입력기는 자동으로 바꾸지 않습니다. 같은 APK가 설치되어 있으면
재설치하지 않습니다. 보조 앱은 네트워크·클립보드·파일 저장 권한을 사용하지 않으며,
텍스트 전달은 Android shell/root 권한으로 제한합니다. 기존 CLI/TUI의 `ADB_INPUT_TEXT`도 지원합니다.
기존 입력기로 돌아가려면 Android 입력기 설정 또는 `adb -s SERIAL shell ime set com.android.adbkeyboard/.AdbIME`를 사용합니다.

### 클립보드와 우클릭

하단 **📋 가져오기**는 Android 기기(VM)의 현재 텍스트 클립보드를 읽어 로컬 클립보드에
복사합니다. 내용 확인 창에서도 직접 선택하거나 **로컬로 복사** 버튼으로 복사할 수 있습니다.
비어 있는 텍스트를 받았을 때는 기존 로컬 클립보드를 지우지 않습니다.

**📋 넣기**는 로컬 텍스트를 기기 클립보드에 저장합니다. 현재 앱의 입력 칸에 자동으로
붙여넣지는 않습니다. HTTPS/localhost의 브라우저 읽기 권한이 없거나 일반 HTTP로 접속했다면
입력 창에서 Ctrl/Cmd+V로 붙여넣고 **기기에 넣기**를 누릅니다.
기기 화면에 포커스한 상태의 Ctrl/Cmd+V는 기존처럼 앱 입력 칸에 바로 붙여넣습니다.

실제 양방향 클립보드 접근에는 scrcpy 연결이 필요합니다. 끊어진 연결이나 응답 시간 초과에서는
이전 캐시를 성공한 읽기로 반환하지 않습니다. Android가 아직 설정되지 않은 클립보드에 응답하지
않는 경우에는 읽기 시간 초과가 표시될 수 있습니다. 연결이 복구되고 기기에서 텍스트를 복사한 뒤
다시 시도하세요. 클립보드 내용은 로그·파일·브라우저 localStorage에 저장하지 않으며 API 응답은
`no-store`로 제공합니다. 자동 복사 알림도 계속 사용할 수 있습니다.

H.264 화면 위의 **우클릭**은 Android 마우스 보조 버튼의 누름·해제로 전달합니다.
Android 앱에 따라 동작이 달라집니다. 브라우저 자체 메뉴는 기기 화면 위에서만 막으며,
파일 탐색 창의 우클릭은 유지합니다. PNG 폴백에서는 우클릭을 전달하지 않고 안내를 표시합니다.

## 독립 redroid/Lima 런타임

```sh
uv run android-console runtime export ./android-runtime
cd android-runtime
./setup-macos.sh
# http://127.0.0.1:8801
```

이미 존재하는 폴더를 덮어쓰지 않습니다. 내보낸 폴더에는 패키지 소스·Dockerfile·Compose·Lima·
선택 IME 설치기가 들어 있어 Git 체크아웃이나 PyPI 등록 없이 Docker 이미지를 빌드할 수 있습니다.
Ubuntu/redroid 이미지와 선택 ADBKeyBoard는 설치 시 검증해서 다운로드합니다.
KakaoTalk APK·로그인 정보·Android 데이터·VM 디스크는 포함하지 않습니다.

Apple Silicon 런타임은 Lima 2.2 이상, VM 이름 `android-console`, CPU 2개·메모리 4GiB·디스크 20GiB,
포트 8801, `/opt/android-console/data`를 사용합니다. 기존 `kakao` VM·포트 8800과 분리됩니다.
VM 홈 마운트·SSH agent 전달은 꺼져 있습니다. `--skip-keyboard`로 IME 설치를 생략할 수 있습니다.
포트 8801은 `static: true`로 고정 전달합니다. 설치기는 입력기 APK의 해시·서명을 확인하고,
Android 입력기 등록과 기본 입력기 선택이 완료된 뒤 진행합니다.
기본 Android 화면은 720×1280·60Hz이며 영상 최대 프레임은 60fps입니다.
60fps는 상한 설정으로, 앱·기기·인코더에 따라 실제 출력은 달라집니다.

일반 Linux는 내보낸 README.md의 binderfs/4K 페이지 커널 준비 안내를 따릅니다.
Google Play나 범용 Android 앱 호환성을 제공하는 추가 레이어는 포함하지 않습니다.

신규 VM 생성부터 입력기 최초 설치, 실제 화면 조작·한글 입력·파일 왕복,
VM 재부팅 후 자동 기동·데이터 유지까지의 결과는
[신규 VM 검증 기록](verification/2026-10-23-android-console-fresh-vm.md)을 참고하세요.

## 공통 코드의 책임

| 공통 패키지 | 책임 |
| --- | --- |
| `adb`, `errors` | 기기 선택, ADB 전송, 범용 오류 |
| `scrcpy`, `video`, `fmp4` | 세션, 제어 프로토콜, H.264 배포, WebCodecs/MSE 영상 |
| `screen`, `worker` | PNG 폴백, 좌표 변환, 입력 순서·완료 보장 |
| `fs`, `webui` | 파일 전송, HTTP API, 하나의 공유 웹 UI |
| `consolelock` | 기기 측 가상 화면 소유권, 이전 CLI와의 호환 |
| `profile` | 앱 실행·브랜딩·기본 모드·내비게이션·캐시 정책 주입 |
| `runtime`, `cli` | 독립 실행, 자체 포함 런타임 내보내기 |

Kakao 어댑터가 `ConsoleProfile`을 전달하고 TUI Worker가 공통 Worker를 확장합니다.
기존 60fps 상한, 포인터 병합, 세로 휠, 한글 입력 순서 보장, 프레임 노후화 제한,
WebCodecs 최신 프레임 표시, MSE 장시간 재생 처리도 같은 공통 구현을 사용합니다.

## 패키징

```sh
uv build --package android-console
uv build --package kakao-cli
```

독립 배포는 android-console wheel 하나만 설치하면 됩니다(`adb`는 별도 설치).
Kakao 배포 시 두 wheel을 함께 제공하거나 공통 패키지를 먼저 게시해야 합니다.
현재 소스 배포·Lima 전송·Docker 빌드는 로컬 공통 패키지 소스를 포함하므로 PyPI 등록이 필요 없습니다.
아직 PyPI에 게시하지 않은 상태에서 root wheel만 단독 배포하지 마세요.

```sh
python -m pip install ./android_console-0.1.2-py3-none-any.whl ./kakao_cli-0.1.6-py3-none-any.whl
```

## VM VPN 공통 관리

`android-console vpn status/login/exit/direct/tui`는 Linux VM의 관리 콘솔을 통해 Tailscale을 제어합니다.
카카오 CLI·TUI·웹 UI와 같은 공통 구현입니다. 기본 주소는 `http://127.0.0.1:8801`이며
`--url`로 변경할 수 있습니다. VM의 선택적 관리 서비스 준비와 인증 흐름은
[VM 전용 Tailscale 가이드](vm-vpn.md)를 참고하세요.

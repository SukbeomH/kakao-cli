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
python -m pip install ./android_console-0.1.0-py3-none-any.whl ./kakao_cli-0.1.3-py3-none-any.whl
```

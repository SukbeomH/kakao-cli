# Android console runtime

카카오톡 코드나 APK 없이 실행하는 독립 redroid 환경입니다.
이 폴더에는 Python 패키지 소스가 포함되어 Git 체크아웃/PyPI 등록이 필요하지 않습니다.

Apple Silicon Mac, Lima 2.2 이상:

```sh
brew install lima
./setup-macos.sh
# http://127.0.0.1:8801
```

CPU 2개·메모리 4GiB·디스크 20GiB의 `android-console` Lima VM을 사용합니다.
기본 mirror 모드, Android 화면 720×1280·60Hz, 영상 최대 60fps입니다.
실제 프레임률은 앱 렌더링과 인코더 성능에 따라 달라집니다.
기존 `kakao` VM·포트 8800·Android 데이터와 분리되어 있습니다.
호스트 홈 마운트와 SSH agent 전달은 꺼져 있습니다.

Ubuntu 및 redroid 이미지는 고정 digest로, 선택 IME(ADBKeyBoard)는 고정 SHA-256과
서명 인증서로 검증합니다. APK/이미지/VM 디스크는 번들에 포함하지 않고 설치 시 받습니다.
ADBKeyBoard는 GPL-2.0이며 출처·버전·해시는 keyboard.json을 참고하세요.
설치를 원하지 않으면 `./setup-macos.sh --skip-keyboard`를 사용하세요.
그 경우 기본 키보드를 사용하며 브라우저 Unicode 글자 입력에는 별도 IME 설정이 필요합니다.

기본 콘솔은 인증 없이 loopback만 바인딩합니다. 원격 접근은 SSH 터널을 사용하세요.
8801은 `static: true`로 고정 전달해 VM 재부팅 시 동적 포트 감지에 의존하지 않습니다.
redroid 컨테이너는 privileged이며 전용 VM에서 실행하세요. Google Play는 포함하지 않습니다.
APK 설치는 `limactl shell --workdir / android-console adb -s 127.0.0.1:5555 install /게스트/APK경로`처럼
ADB로 수행합니다. 설치할 APK는 게스트에 별도로 복사해야 합니다.

```sh
./lima-compose.sh ps
./lima-compose.sh logs --tail 100 webui
limactl stop android-console
```

Linux는 4K 페이지 커널·Docker Compose·binderfs 세 장치가 준비된 전용 호스트에서
`sudo mkdir -p /opt/android-console/data` 후 `docker compose up -d --build`를 사용합니다.
호스트에 따라 binder_linux 모듈 설치 및 binderfs 구성이 먼저 필요합니다(lima.yaml 참고).
Apple Silicon용 64only 이미지는 compose.lima.yaml에 고정되어 있습니다.
데이터는 `/opt/android-console/data`에 유지됩니다. 컨테이너 재생성 시 같은 경로를 사용하세요.

compose/Lima/binderfs 구성의 원저작권 고지는 python-package/src/android_console/NOTICE를 참고하세요.

# android-console

ADB 기기를 브라우저에서 조작하는 독립 Python 패키지입니다. H.264(WebCodecs/MSE),
PNG 폴백, 실시간 포인터·세로 휠·키보드·클립보드, 파일 탐색/전송을 제공합니다.
Python 의존성은 없으며 외부 `adb`가 필요합니다. scrcpy-server는 최초 실행 때
공식 배포본을 다운로드하고 고정 SHA-256으로 검증합니다.

```sh
pip install .
android-console serve --serial DEVICE_SERIAL
# TCP ADB 기기
android-console serve --connect 127.0.0.1:5555 --serial 127.0.0.1:5555
```

기본 주소는 http://127.0.0.1:8801 이며 기본 모드는 기존 화면의 미러링입니다.
Home·최근 앱 버튼을 포함하며 특정 앱을 자동 실행하지 않습니다.
별도 가상 화면에서 앱을 열려면 `--video-mode virtual --app PACKAGE`를 사용합니다.
한글 입력은 기기에 ADBKeyBoard 설치가 필요합니다.

독립 redroid/Lima(Lima 2.2 이상) 실행 구성은 `android-console runtime export ./android-runtime`으로
내보냅니다. 내보낸 폴더의 README.md를 따르세요. CPU 2개, 메모리 4GiB, 디스크 20GiB,
포트 8801, 별도 데이터 디렉터리를 사용합니다.

인증 기능은 없습니다. 원격 접근은 loopback 바인딩과 SSH 터널을 권장합니다.
기존 kakao-cli는 이 패키지의 공통 기능을 사용하며 앱 전용 정책만 별도 유지합니다.

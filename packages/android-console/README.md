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
조합이 끝난 글자는 즉시 보내고, 전송 중 쌓인 글자만 합쳐 입력 순서를 유지합니다.
한글 조합 중 글자도 Android 입력 칸에 갱신합니다. 첫 사용 때 번들에 포함된 MIT 입력 보조 앱을
선택한 기기에 설치·선택합니다(기존 ADBKeyBoard 사용 기기). 일반 사용자가 고른 다른 입력기는
자동 전환하지 않습니다. 조합 중 Enter는 확정만 하고, 글자를 중복 추가하지 않습니다.
PNG 캡처도 입력 처리와 분리합니다. 전송 실패 시 대기 입력을 자동 재전송하지 않으므로
기기 내용을 확인한 뒤 다시 입력하세요.

하단 **📋 가져오기 / 📋 넣기**로 기기와 로컬의 텍스트 클립보드를 교환합니다.
넣기는 클립보드만 저장하며 앱 입력 칸에 자동으로 붙여넣지 않습니다.
일반 HTTP나 클립보드 권한 거부 시에는 복사·붙여넣기 창을 사용합니다.
기기 화면의 우클릭은 H.264 모드에서 Android 마우스 보조 버튼으로 전달합니다.

독립 redroid/Lima(Lima 2.2 이상) 실행 구성은 `android-console runtime export ./android-runtime`으로
내보냅니다. 내보낸 폴더의 README.md를 따르세요. CPU 2개, 메모리 4GiB, 디스크 20GiB,
포트 8801, 별도 데이터 디렉터리를 사용합니다.

인증 기능은 없습니다. 원격 접근은 loopback 바인딩과 SSH 터널을 권장합니다.
기존 kakao-cli는 이 패키지의 공통 기능을 사용하며 앱 전용 정책만 별도 유지합니다.

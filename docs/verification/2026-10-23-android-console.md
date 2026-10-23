# android-console 분리 검증

환경: Apple Silicon Mac, Python 3.13 개발 환경 / Docker Python 3.11,
기존 Lima `kakao` VM의 Android 14 redroid. 실제 휴대전화는 사용하지 않았다.

## 자동 검증

- 전체 pytest: **215 passed**, 72.83초.
- 신규 검증: 공통 클래스·모듈 동일성, Kakao 정책 유지, Kakao/cryptography import 차단 상태의
  독립 import, 입력 순서·오류 전달·Worker 종료, HTTP Home/최근 앱 입력,
  일반 미러 모드의 앱 자동 실행·잠금 취득 없음, 가상 화면 소유권 충돌과 안전한 해제,
  세션별 서버 JAR 정리, 복수 기기 선택, Unicode 문자열의 셸 인용, 런타임 내보내기.
- 브라우저 fixture: Kakao·일반 페이지 각각 1200×800, 390×844, 640×360에서 파일 팝업,
  화면과 파일 목록의 휠 분리, 포커스·Esc·닫기·배경 클릭 통과.
- 실제 공통 `wcSink` 검증: 최신 프레임 표시, 오래된 세션 무시, 과부하·종료·그리기 실패 처리 통과.
- 내보낸 Lima YAML: `limactl validate` 통과. 내보낸 Compose + Lima override:
  Docker Compose `config --quiet` 통과. Bash 스크립트 구문 검사 통과.

## 독립 설치·실행

- `uv build --package android-console`: wheel·sdist 빌드 통과.
- 새 가상환경에 독립 wheel만 설치한 뒤 `python -I` import 통과.
  `kakao_cli`·`cryptography`가 설치되지 않았음을 확인했다.
- wheel에 NOTICE·Lima/Compose/설치 리소스가 들어 있고 Python 의존성이 없음을 확인했다.
- 설치된 wheel의 `runtime export`로 자체 포함 소스 폴더를 만든 뒤 독립 Docker 이미지 빌드 통과.
  Docker 이미지에서도 Kakao/cryptography가 없음을 확인했다.
- 독립 컨테이너를 기존 테스트용 redroid에 명시적으로 연결하고, 별도 포트 8801을 사용했다.
  Chrome 475×988에서 미러 H.264 실제 디코딩·그리기(800×1280), Home·최근 앱 버튼,
  파일 팝업을 확인했다. 가로 넘침과 페이지 JavaScript 오류는 없었다.
  실제 입력 버튼은 기기에 누르지 않고 HTTP fake-device 테스트로 검증했다.
- 최초 분리 검증 시에는 신규 Lima VM 전체 부팅·IME 최초 다운로드 설치를 수행하지 않았다.
  이후 [신규 VM 설치·재부팅 검증](2026-10-23-android-console-fresh-vm.md)을 별도로 완료했다.

## Kakao 호환 배포

- 로컬 공통 소스를 함께 설치하는 기존 Kakao Docker 이미지 빌드 통과.
- 이미지에서 `kakao_cli.fs is android_console.fs`, Kakao 페이지의 Home 버튼 없음 확인.
- 기존 로컬 `8800` 서비스를 공통 패키지를 사용하는 이미지로 교체한 뒤 Chrome 475×988에서
  가상 화면의 H.264 실제 디코딩·그리기(800×1280)를 확인했다.
  Home·최근 앱 버튼은 숨겨져 있었고 가로 넘침·페이지 JavaScript 오류는 없었다.
- 소스 릴리즈 tarball 생성 후 공통 pyproject·웹 UI·런타임 리소스 포함 확인.
  APK·서버 JAR·VM 디스크·개인 키·DB 미포함 확인.
- GitHub 게시·버전 태그·기존 공개 릴리즈 수정은 이 작업에서 수행하지 않았다.

분리 자체의 속도 향상을 주장하지 않는다. 기존 영상·입력 최적화는 공통 코드로 이동했으며,
같은 구현을 Kakao 콘솔과 독립 콘솔에서 사용한다.

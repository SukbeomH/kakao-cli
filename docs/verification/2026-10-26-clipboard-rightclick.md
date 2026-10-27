# 양방향 클립보드 버튼과 우클릭 전달

## 동작

공통 웹 UI에 **📋 가져오기 / 📋 넣기**를 추가했다. Kakao 콘솔과 독립 Android 콘솔이
같은 코드를 사용한다. 가져오기는 기기의 텍스트 클립보드를 요청해 로컬로 복사하며,
넣기는 로컬 텍스트를 기기 클립보드에 저장한다. 넣기 버튼은 앱 입력 칸에 자동으로 붙여넣지 않는다.
화면 포커스 중 Ctrl/Cmd+V의 기존 붙여넣기는 유지한다.

권한이 없거나 비보안 HTTP로 접속하면 복사·붙여넣기 창을 사용한다. 읽은 텍스트가 비어 있으면
로컬 클립보드를 지우지 않는다. 전송 실패 시 입력 내용을 유지해 다시 시도할 수 있다.
창을 닫으면 텍스트 필드를 비우며 API 응답에 `Cache-Control: no-store`를 지정했다.

scrcpy v5.0.1은 자동 동기화가 켜져 있으면 명시적 GET_CLIPBOARD 요청에 답하지 않는다.
이를 끄고 직렬화한 읽기 요청과 제어 채널의 주기적 조회로 수동 가져오기와 기존 복사 알림을 제공한다.
미설정 클립보드는 응답이 없을 수 있어 시간 초과로 처리한다. 백그라운드 조회 시간 초과만으로는
영상을 재시작하지 않는다. 시간 초과로 응답 순서가 불명확해진 연결에서 수동 읽기를 요청하면
연결을 다시 맺고 요청해 이전 응답이나 캐시를 성공한 수동 읽기로 사용하지 않는다.
이 과정에서는 화면 연결이 잠시 재설정될 수 있다.
참고: [scrcpy v5.0.1 Controller](https://github.com/Genymobile/scrcpy/blob/v5.0.1/server/src/main/java/com/genymobile/scrcpy/control/Controller.java).

화면의 우클릭은 Android 마우스 보조 버튼의 DOWN/UP으로 전송한다(pointer ID -1,
button/action button 2). 길게 누르기나 Back 키로 바꾸지 않는다. H.264 영상 모드에서만
전달하며 PNG에는 안내를 표시한다. 파일 탐색 창의 브라우저 메뉴는 유지한다.
프로토콜 기준은 [scrcpy v5.0.1 제어 메시지](https://github.com/Genymobile/scrcpy/blob/v5.0.1/app/src/control_msg.c)다.

## 검사

- 전체 pytest: **227 passed**, 74.81초.
- 마지막 잠금 범위 정리 후 소켓 검사: **6 passed**, 0.16초.
- 실제 socket pair로 GET 요청, 동일 문자열 재조회, 빈 문자열, 읽기 시간 초과,
  백그라운드 미응답 시 영상 연결 유지, 불명확한 연결 재설정 후 새 응답 사용,
  클립보드 저장 ACK와 송신 실패 후 정리를 확인했다.
- HTTP API의 클립보드 읽기·연결 해제·시간 초과, JSON/Origin 보호,
  우클릭의 영상 좌표 변환·보조 버튼 바이트·PNG 차단을 확인했다.
- 실제 입력 처리 JavaScript의 우클릭, 드래그·세로 휠·한글·Enter 순서 회귀 검사를 통과했다.
- Chrome fixture에서 Kakao/Android 두 페이지 각각 localhost와 일반 HTTP를 검사했다.
  기기→로컬 복사, 로컬→기기 저장(paste=false), 권한 거부 대체 창, 빈 값/오류,
  재시도, Esc 닫기·내용 제거, 우클릭 영역 제한을 확인했다.
- 두 페이지 각각 1200×800, 390×844, 640×360에서 기존 파일 팝업의
  휠 분리·포커스·닫기 동작 회귀 검사를 통과했다.
- 공통 패키지 wheel·sdist 빌드와 Python 구문 검사를 통과했다.

브라우저 검사는 클립보드 API와 HTTP를 fixture로 대체했고 실제 호스트 클립보드나
Android 기기를 읽거나 변경하지 않았다. 이 최초 검사 시점에는 두 VM을 종료 상태로 유지했다.

## 후속 실기기 검증·이미지 재빌드

사용자의 후속 요청에 따라 `android-console` VM만 기동하고 최신 공통 소스로
`android-console:local` 웹 콘솔 이미지를 다시 빌드·적용했다.
기존 Android 데이터와 볼륨을 유지했으며 검증을 마친 뒤 VM과 웹 콘솔은 실행 상태로 남겼다.
`kakao` VM은 종료 상태를 유지했다. 접속 주소는 http://127.0.0.1:8801/ 이다.

실제 Chrome 창, macOS 시스템 클립보드, Android 14의 설정 검색 입력 필드로 검증했다.
브라우저 클립보드 API나 HTTP 응답을 mock하지 않았다.

| 검증 | 실제 확인 결과 |
| --- | --- |
| 로컬→Android | macOS `pbcopy`에 넣은 한글·이모지를 Chrome의 native clipboard API로 읽어 **📋 넣기**로 전송 |
| 자동 붙여넣기 방지 | 저장 직후 Android 입력 필드가 변경되지 않음 |
| 기기 클립보드 저장 | 웹 API를 쓰지 않는 Android `KEYCODE_PASTE`로 입력 필드에 붙여넣은 텍스트가 원문과 일치 |
| Android→로컬 | Android 입력 필드에서 Ctrl+A·`KEYCODE_COPY`로 다른 테스트 문구를 복사하고 **📋 가져오기** 실행 |
| 맥 클립보드 도착 | macOS `pbpaste`와 Chrome native clipboard API가 모두 Android에서 복사한 원문과 일치 |
| 우클릭 | 실제 브라우저 마우스 오른쪽 버튼으로 화면을 클릭해 Android InputDispatcher에 `source=MOUSE`, `buttonState=0x00000002` 도착 |
| 버튼 해제 | `DOWN → BUTTON_PRESS → BUTTON_RELEASE → UP` 순서, 보조 버튼의 `actionButton=0x00000002` 확인 |
| 앱 UI 결과 | Android의 Undo·Cut·Copy·Paste·Share 메뉴가 실제 화면에 표시됨 |
| 브라우저 오류 | JavaScript 오류 없음 |

맥의 기존 pasteboard 항목·형식은 AppKit으로 메모리에 보관하고 테스트 후 복원했다.
기존 클립보드 내용은 로그·파일에 기록하지 않았다. Android 쪽에 테스트 문자열이 남았던 경우
검증 후 빈 텍스트로 정리하고 테스트 입력 필드도 비웠다.

실행 이미지: `sha256:4a4c09741669a80e6601069376997bcf4aa6c040eb4d74b8d9836b8b2a7a90be`.
입력 이벤트와 검사 결과는 [실기기 검증 데이터](2026-10-26-clipboard-rightclick-live.json)에 있다.

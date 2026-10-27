# 한글 마지막 음절이 늦게 보이는 문제

검증일: 2026-10-26.

## 원인과 변경

기존 웹 입력은 `input.isComposing`인 동안 전송하지 않고 `compositionend`에서만
ADBKeyBoard의 `commitText` 경로로 보냈다. 다음 글자를 입력하거나 공백·Enter로 조합을
끝내기 전까지 현재 음절이 VM에 나타나지 않는 구조였다. 앞서 고친 60ms 전송 대기와는
별개이며, 단순히 타이머를 더 줄여서는 해결되지 않는다.

자체 MIT 입력 보조 앱 **Android Console Input**을 추가했다. 브라우저가 조합의 시작·갱신·확정을
순서대로 전달하고, 보조 앱은 Android `InputConnection.setComposingText`로 같은 조합 영역을
교체한다. 종료 때 `commitText`로 한 번 확정한다. `ㅎ → 하 → 한`을 글자 세 개로 추가하거나,
기존 글자를 Backspace로 지운 뒤 다시 쓰는 방식은 사용하지 않는다.

전송 중 쌓인 조합 갱신은 최신 스냅샷 하나로 대체한다. 시작·확정·일반 글자·Enter·붙여넣기
경계를 유지하고, 조합 중 Enter는 브라우저의 확정에만 사용한다. 화면을 클릭해 입력 위치를
바꾸거나 붙여넣을 때에는 기존 조합의 완료를 먼저 기다린다.

Android 입력 칸이 바뀌면 이전 조합 세션을 폐기한다. 다른 브라우저가 진행 중 조합을
빼앗거나, 이미 확정한 세션을 다시 갱신하는 요청도 거부한다. 오류가 난 글자와 대기 중 Enter는
자동 재전송하지 않는다. 클립보드는 일반 입력이나 조합 전달에 사용하지 않는다.

이벤트 기준: [MDN compositionend](https://developer.mozilla.org/en-US/docs/Web/API/Element/compositionend_event).
Android API: [InputConnection](https://developer.android.com/reference/android/view/inputmethod/InputConnection).

## 설치와 배포

보조 앱은 `org.androidconsole.ime`, Android 8(API 26) 이상용이다. 자체 소스·빌드 스크립트·MIT
라이선스·서명한 약 16KiB APK·SHA-256을 공통 Python 패키지에 함께 포함한다. 제3자
ADBKeyBoard/KakaoTalk APK는 기존처럼 설치 시 다운로드하며 이 번들에 넣지 않는다.
릴리즈 패키징도 정확히 지정한 자체 보조 앱과 일치하는 해시만 허용하도록 검사한다.

첫 조합 요청에서 현재 선택한 ADB 기기의 입력기가 ADBKeyBoard 또는 자체 보조 앱인지
확인한 뒤 설치·선택한다. 사용자가 선택한 다른 입력기를 자동 변경하지 않는다.
같은 APK 해시가 설치돼 있으면 재설치하지 않는다. 새 설치 뒤 Android 입력기 목록의 등록과
선택 완료를 기다리고, 수신자 준비 중에는 글자가 없는 시작 요청만 재시도한다.
글자 갱신·확정은 재시도하지 않는다.

보조 앱의 수신자는 Android shell/root의 `DUMP` 권한을 요구한다. 네트워크·클립보드·파일
저장 및 입력 내용 로깅은 하지 않는다. 기존 CLI/TUI의 `ADB_INPUT_TEXT`, `ADB_CLEAR_TEXT`도
지원하므로 보조 앱 선택 후에도 기존 Unicode 입력을 사용할 수 있다.

## 실제 검증

기존 `kakao` VM은 종료 상태로 유지했다. 별도 `android-console` VM(2 CPU/4GiB/20GiB)의
Android 설정 검색창과 Chrome 475×988에서 검사했다. Chrome CDP의 `Input.imeSetComposition`과
`Input.insertText`로 실제 브라우저 조합 이벤트를 발생시켰다. HTTP 응답·Android 입력기는
mock하지 않았으며 각 단계에서 Android UI XML을 읽어 문자열을 확인했다.
macOS 입력기별 물리 키 입력 검사는 이번 범위에 포함하지 않았다.

| 검사 | 결과 |
| --- | --- |
| 조합 중 표시 | 종료 전에도 `ㅎ → 하 → 한`이 Android 입력 칸과 일치 |
| 받침 수정 | `한 → 하 → ㅎ → 한`이 같은 영역에서 교체됨 |
| 연속 음절 | `한국`으로 갱신한 뒤 확정해도 `한국` 한 번만 남음 |
| 조합 취소 | `한국` 뒤의 조합 중 `어`를 취소하면 앞의 `한국` 보존 |
| 일반 Unicode 호환 | 보조 앱 선택 후 일반 입력 ` ABC🙂`도 정확히 추가됨 |
| 미러/PNG | 실제 Android 입력 칸에서 조합 중 갱신·확정·취소 통과 |
| 가상 화면/WebCodecs | 별도 가상 display의 설정 검색창에서도 같은 검사 통과 |
| 새 설치 | 자체 보조 앱을 제거한 상태에서 첫 요청으로 설치·등록·선택 후 입력 성공 |
| 세션 보호 | 다른 브라우저의 조합 시작·확정된 세션 재사용·Android 포커스 상실 뒤 갱신을 실제 API에서 거부 |

새 설치 시 첫 글자 갱신의 HTTP 완료는 **542ms**였다(설치·선택 포함).
이후 같은 실행의 조합 갱신 응답은 **19~32ms**, 조합 취소/새 세션 포함 **19~44ms**였다.
영상에서 글자가 보일 때까지의 시간이 아닌, 브라우저 입력부터 HTTP 처리 응답까지의 단회 측정이다.

테스트는 설정 앱에서만 진행했고 KakaoTalk 대화나 실제 휴대전화, macOS 클립보드를 사용하지 않았다.
검증 후 테스트 입력을 정리하고 두 VM을 모두 종료했다.

## 회귀 검사

- 전체 pytest: **243 passed**, 75.29초.
- JavaScript 입력 검사: 최신 조합 스냅샷 병합, 확정 한 번, 취소, Enter 방지,
  입력 위치 변경 전 확정 대기, 붙여넣기 순서, 실패 시 재전송 금지.
- HTTP: 세션/단계/Unicode 크기 검증, 동일 출처 보호, 순서 보장 Worker 호출과 오류 반환.
- 설치: 사용자 입력기 선택 보존, 동일 APK 재사용, 선택된 보조 앱의 업그레이드 순서,
  최초 입력기 등록 대기, 빈 시작 요청만 재시도.
- 패키징: 자체 APK 해시 확인, 제3자 APK·개인 상태 제외, APK 서명 검증.
- Chrome fixture: Kakao/Android 각각 클립보드·일반 HTTP/권한 거부 대체 창·우클릭 회귀 검사,
  1200×800/390×844/640×360 파일 팝업 검사 통과.

검증 스크립트: `tests/verify_ime_live.cjs`(별도 `android-console` VM과 설정 앱 전용).
실측 데이터: [JSON](2026-10-26-korean-preedit.json).

Kakao는 공통 웹 UI/Worker를 사용한다. 종료된 `kakao` VM의 기존 이미지는 바꾸지 않았으므로,
다음 기동 때 `limactl start kakao` 후 `./scripts/lima-compose.sh up -d --build`로 반영한다.

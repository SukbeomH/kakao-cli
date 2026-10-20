# 후속 5: 프로필 이름·메시지 종류

## 범위와 상태

기준 SHA `989301ae5c47ab332762de0c68a3acfd87c64342`, 작업 브랜치 `codex-db`.
시작 작업트리는 깨끗했다. 라이브는 이미 compose 전환된 상태이며 이번 작업에서는
`~/kakao-compose`, 기존 배포 소스/파일, 컨테이너, ADB root, 포트, 웹 서비스를 바꾸지 않았다.

**구현 완료:** 확정 메시지 종류/삭제·가림 상태, 기존 평문 오픈채팅 이름 보완.
**미완료·의존성 검토 대기:** 새 암호화 프로필 DB의 이름 조회.
새 프로필 조회가 구현됐거나 이름 확인률이 그 DB 덕분에 개선됐다고 주장하지 않는다.

## 프로필 DB 확인과 의존성 게이트

기기 내부 `sqlite3 -readonly -json`만 사용했다. crypto_user_database와 crypto_database의
`SELECT count(*) FROM sqlite_master`는 모두 `file is not a database`로 거절됐다.
내장 SQLite의 `PRAGMA cipher_version`은 비어 있다. 오류가 stdout에 오면서 adb 종료 코드가
0일 수도 있어서 반환 코드만으로 성공이라고 판단하지 않고, 실제 JSON 파싱으로 확인했다.
현재 DB 모듈도 JSON 파싱 실패를 DBUnavailable로 처리하며 해당 반례 테스트를 추가했다.

1차 구현 [kakaotalk-bridge @097a9b8 CryptoProfiles.kt](https://github.com/rokrokss/kakaotalk-bridge/blob/097a9b8/iris/CryptoProfiles.kt)는
SQLCipher SQLiteDatabase로 crypto_user_database를 OPEN_READONLY로 열어 user/talk_channel을
조회한다. 일반 SQLite 거절과 이 1차 구현은 SQLCipher 엔진 필요성의 근거지만,
현재 기기의 실제 키/암호 파라미터로 열린다는 실증까지 한 것은 아니다.

필요한 의존성은 **Android 기기 내부에서 실행하는 SQLCipher4 호환 읽기 전용 엔진**이다.
호스트 Python SQLCipher 패키지로 DB를 반출해서 읽는 방식은 허용하지 않는다.
버전·ABI·출처·해시·라이선스, 손상 시 DB 삭제 금지, 키의 argv/환경/로그 노출 금지를
포함한 설계를 Hermes가 먼저 검토해야 한다. 새 의존성 추가·다운로드·빌드·기기 배치와
키 파일 접근은 하지 않았다. 기존 cryptography 의존성은 그대로다.

Hermes용 보고서는 `/home/agent/kakao-tasks/REPORT_profile_dependency_codex.md`에 작성했다.
사용자에게 재승인을 묻지 않고 이 부분만 대기 상태로 기록하고 독립 가능한 작업을 진행했다.

## 구현

- 확정 type 라벨: system/text/photo/video/sticker/file/animated_sticker/
  animated_sticker_ex/reply/multi_photo/leverage/alimtalk.
- 원본 type 보존 + base_type/base_kind/is_deleted/is_blinded/is_secret 필드 추가.
- 삭제 비트0x4000 또는 sentinel -11은 deleted, 가림 비트0x8000 또는 -13은 blinded.
  삭제/가림 행의 남은 암호문을 복호화하지 않고 고정 표식으로 대체한다.
- 미확정 숫자와 미확정 상위 비트는 unknown(n) 유지. 현재 관측되지 않은 모든 enum을
  무작정 지원 목록에 넣지 않았다. 시스템 세부 사건, 답장의 인용 원문/첨부는 확장하지 않았다.
- 기존 KakaoTalk2.db.open_link(id,name)에서 명시적인 링크 ID로 OM/OD 방 이름을 찾는다.
  사용자 지정 메타 우선순위를 유지한다. 동일 ID 중복/빈 이름/필수 열 부재/조회 실패는
  선택적 이름을 쓰지 않으며, 그 이유로 본문 수신을 UI로 바꾸지 않는다.
- 본문·이름·ID 원본은 fixture/로그/문서에 넣지 않았다. 테스트 데이터는 직접 만든 가짜 값이다.

## 1차 APK 근거

기존 수집본 `/home/agent/kakao-artifacts/android/apkm/base.apk`:

- 메타데이터: 26.8.3 / versionCode 29260830, 실기 버전 가드와 동일.
- SHA-256: `c6013aef31e7510a48f56783430428c3d61cada7ac8d4c23ecb9346a9c491519`.
- 기존 JADX 1.5.6 출력 `jadx-base/sources/defpackage/tg9.java`,
  원본 Kotlin 메타데이터 `ChatMessageType.kt`, classes10.dex.
- 해당 enum/Companion의 원시 타입 값과 삭제/가림/비밀 플래그만 확인했다.
  앱 APK나 역컴파일 코드를 저장소에 복사하지 않았다.
- enum 출력 SHA-256: `582eb967972a04ab24981c727ef9dffd8dcc75305b21af3ace34d58b0cbe77af`.
- REA의 inspect_android_package는 JADX provider 환경변수 미설정으로 unavailable였다.
  REA/호스트 설정을 바꾸거나 설치하지 않고 기존 로컬 JADX 출력의 정적 증거로 진행했다.
  이 근거는 REA가 생성한 Evidence가 아니다. 수집본의 서명/실기 APK 바이트 동일성까지
  새로 인증한 것은 아니며, 다른 버전에는 기존 버전 가드를 적용한다.

## 검증 결과

- TDD: 합성 계약19개를 먼저 작성해 **18 실패/1 통과** 확인 후 구현.
  추가 집계 출력 허용목록도 합성 테스트3개 실패 확인 후 구현했다.
- 제품/수신 단위 테스트88개 통과, 중간 전체 회귀 **133 통과(56.75초)**.
- 최종 전체 회귀 **136 통과(56.70초)** = 기존104 + 신규32. 출력 허용목록의 정상
  집계 허용/가짜 이름·본문 거절을 보강한 뒤 해당 신규32개도 다시 통과(0.04초).
- 실기: 코드만 SSH stdin으로 전달해 원격 Python 메모리에서 기준/후보를 실행했다.
  배포 경로에 소스나 helper를 설치하지 않았다. UI dump/탭/입력/잠금 쓰기 없음.
- 방55개: 이름 확인14개(25.45%) → 17개(30.91%), 오픈링크 이름4개,
  기존 멤버 이름 캐시45개. 신규 SQLCipher 프로필 이름 조회는0회.
- 메시지1,135행: 해석1,135 / 실패0 / 현재 분포 unknown0.
  system15, file21, reply35, 삭제3, 가림0.
- 삭제·가림을 제외한 본문 있는1,103행:1,103 성공. 미삭제 행의 본문과 is_mine이
  기준 코드와 동일=true. 삭제3행은 원문을 복호화하지 않고 표식 처리했다.
- 라이브 redroid/webui 시작 시각 유지=true, 둘 다 재시작0.
- 검증 출력은 숫자·불리언·공개 타입 라벨/분포만 허용하며 독립 허용목록으로 재검사한다.
  실제 본문/이름/키/사용자 ID는 출력하지 않는다.
- 상세 타입 분포/수신 계약은 [DB 문서](../db-receive.md)에 기록했다.

## 남은 작업

1. Hermes의 기기 내부 SQLCipher 엔진 의존성 검토 후 새 user/talk_channel 이름 조회 구현·실증.
2. 새 프로필이 열리기 전까지 남은38개 방은 ID fallback 또는 기존 UI 경로가 필요하다.
3. 가림·비밀·음수 sentinel은 현재 실기 사례가 없어 합성 테스트만 통과했다.
4. 새 코드는 운영 서비스에 배포하지 않았다. 검토 후 배포도 별도 운영 작업이다.

전송0, DB 사본 반출0, 민감정보 기록0, 라이브 서비스 수정/재시작0, 새 의존성0.
ADB 키 인증과 Mac Lima 기동/로그인은 수행하지 않았다. 푸시/머지도 하지 않는다.

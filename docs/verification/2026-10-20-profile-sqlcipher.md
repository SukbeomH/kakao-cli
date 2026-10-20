# 후속 5 추가 승인 — 기기 내부 SQLCipher 프로필 실증

사용자의 `TASK_profile_types.md` 추가 승인(2026-10-20 23:10 UTC)에 따라
공식 엔진을 도입했고 **실키로 제자리 OPEN_READONLY 조회에 성공**했다.
기준 커밋은 `81fe2af8311f4443751d4f1c350563584f103363`, 작업 브랜치는 `codex-db`다.
구현·합성 테스트 커밋은 `a2df2f3`이다.
이전 의존성 대기 보고와 타입 구현 보고는 당시 결과로 보존한다.

## 구현과 실기 결과

`ProfileReader.java`는 기기 안에서만 키를 도출해 `crypto_user_database`를 열고,
`user`/`talk_channel`의 ID와 이름만 메모리 파이프로 반환한다. 호스트 `profiles.py`가
엄격한 형식·행 수·중복 ID·길이 검증을 거친 뒤 `Database`의 메모리에만 보관한다.
기존 메타·오픈채팅 별칭을 우선하며, 채널은 chat_id와 참가자 또는 실제 발신자 ID가
동시에 맞는 단일 후보에만 연결한다. 모호한 이름을 추정하지 않는다.

실기 대상은 기존 compose의 Android14/SDK34 x86_64, Kakao26.8.3이다.
기준/변경 Python 소스는 SSH stdin에서 격리 모듈로 메모리에 로드했다.
운영 배포 경로와 서비스 설정은 변경하지 않았다.

| 검증 | 결과 |
|---|---:|
| 엔진 정상 JSON / 실제 isReadOnly | true / true |
| 엔진 stderr 존재 | false |
| 프로필 이름 / 채널 이름 | 347 / 59 |
| 방 수 | 55 |
| 이름 있는 방: 기준 → 변경 | 17 → 55 |
| 메시지 행 | 1,135 |
| 이름 있는 발신자 행: 기준 → 변경 | 536 → 1,025 |
| 본문·kind 기준 일치 / is_mine 기준 일치 | true / true |
| 메시지 해석 실패 | 0 |
| helper 임시 경로 부재 확인 | true |

실제 이름·본문·키는 이 문서나 fixture에 넣지 않았다. 이름 수치는 비어 있지 않은
DB 이름의 ID 조인 결과이며 UI 표시와의 문자열 대조는 아니다.
발신자110행은 여전히 기존 ID fallback을 사용한다. 현재55개 방의 성공을 다른 계정·버전에
일반화하지 않는다. 엔진이 실패하면 기존 이름·ID를 유지하며 선택적 프로필 실패만으로
UI를 열거나 서비스 복구를 시도하지 않는다.

첫 실기 시도에서는 로깅 비활성 확인 코드가 공식 반환 형식과 맞지 않아
`open_readonly/security`로 중단했다. 원문 예외/키를 출력하지 않았고 임시 경로를 정리했다.
공식 API에서 로그 수준 NONE의 숫자 반환0을 확인한 후 preKey에서
`PRAGMA cipher_log_level=NONE`을 설정하고0을 검증하도록 수정했다.
이는 helper 프로세스의 로깅 설정이며 DB 쓰기 pragma가 아니다.
수정 후 실키 조회·회귀·정리를 반복 확인했다. 실패 시 rekey/복구/삭제/사본 생성은 없었다.

## 출처·고정 해시·재현 빌드

엔진은 [Zetetic 공식 SQLCipher Android v4.10.0](https://github.com/sqlcipher/sqlcipher-android/tree/v4.10.0),
BSD-3-Clause다. 공식 Maven POM이 선언한 API 의존 AndroidX SQLite2.2.0(Apache-2.0)도
동일하게 고정했다. 비공식 포크와 다른 SQLCipher 엔진은 사용하지 않는다.

| 공개 산출물 | SHA-256 |
|---|---|
| sqlcipher-android-4.10.0.aar | `cc60b1a40d023bec06a1e56740db7172d2516668570140cd9de00ca90f84cd9f` |
| androidx.sqlite:sqlite:2.2.0 AAR | `6156d5d2c17bd8c5460f199142e4283053b1da750994f6b396c62c50fcc7270c` |
| x86_64 libsqlcipher.so | `482f62c4d77e136d7cb7eb30d74a5c4cbadc3577d10ced1f13f3e745e1be7e7a` |
| 자체 profile-helper-v1.jar | `165b5b7e69acbfe4245be9b8a0f6fbbac4ee0cc101fa2691924c15956d5f464a` |

다운로드 경로는 [공식 Maven Central AAR](https://repo.maven.apache.org/maven2/net/zetetic/sqlcipher-android/4.10.0/sqlcipher-android-4.10.0.aar)와
[Google AndroidX AAR](https://dl.google.com/dl/android/maven2/androidx/sqlite/sqlite/2.2.0/sqlite-2.2.0.aar)다.
SQLCipher AAR의 계산 해시는 공식 배포 `.aar.sha256`과도 일치했다.
다른 ABI의 추출 라이브러리 해시는 `profiles.py`에 별도 고정했지만 실기 검증은 x86_64만 했다.
NOTICE와 소스 리소스에 라이선스 전문을 두었고 helper에도 META-INF로 포함했다.

공개 배포물과 빌드 중간물은 저장소 밖 `~/.cache/kakao-cli/sqlcipher-4.10.0/`에 둔다.
자체 helper는 Java8 타깃, D8 release/min-api26, 정렬된 ZIP 항목·고정 타임스탬프로 빌드한다.
실측 도구는 `javac 21.0.12.1`, D8 `9.2.4-dev`
(build `084a89126ae0596c599143df57a654646af28313`)다.
같은 도구로 반복 빌드해 고정 helper 해시의 일치를 확인했다.

```bash
uv run --python .venv/bin/python scripts/build-profile-helper.py \
  --d8 /home/agent/kakao-tools/build-tools/android-37.0/d8
```

도구/소스가 달라 빌드 해시가 맞지 않으면 helper 설치와 사용을 거절한다.
기존 캐시 변조도 거절하며 자동으로 덮어써 신뢰하지 않는다.
Java/D8은 빌드 도구이며 Python 프로젝트 의존성/uv.lock 변경은 없다.
wheel/sdist에는 Java 소스·NOTICE·두 라이선스가 들어가며 AAR/so/dex/class/jar는 없다.
`.gitignore`/`.dockerignore`에서도 해당 바이너리를 제외한다.

## 읽기 전용·비노출 계약

- root는 기존 기기 권한을 확인할 뿐 요청하지 않는다. helper 전용 프로세스에만
  core dump 비허용(`prctl`, `ulimit`)과 Java NoopTarget/SQLCipher 로그 NONE을 적용한다.
- 지정된 DataStore 파일의 `userDbPassPhraseSalt`와 `userDbPassPhraseWhenFailed`만
  키 형식으로 해석한다. 기기 안에서만 fallback 또는 PBKDF2-HMAC-SHA256으로 도출하고
  native byte[] 인자로 전달한다. argv/환경변수/SQL 키 문자열/파일/stdout/logcat에 키를 넣지 않는다.
  프로필 형식은 MIT [bridge ProfileData.kt @097a9b8](https://github.com/rokrokss/kakaotalk-bridge/blob/097a9b8/iris/ProfileData.kt),
  [CryptoProfiles.kt](https://github.com/rokrokss/kakaotalk-bridge/blob/097a9b8/iris/CryptoProfiles.kt)를 참고해
  직접 구현했다. 소스의 KDF 상수는 공개 형식 상수다. 실제 preference/키 버퍼는 finally에서 비운다.
- `SQLiteDatabase.openDatabase`의 플래그는 `OPEN_READONLY | NO_LOCALIZED_COLLATORS`(1|16).
  `isReadOnly()`를 확인하고 실제 열/스키마를 zero-row SELECT로 검증한다.
  Room/openOrCreate/쓰기 SQL/마이그레이션/rekey/VACUUM/체크포인트/복사 코드는 없다.
  기본 corruption handler 대신 즉시 거절하는 handler를 사용해 삭제·복구 경로를 차단한다.
- 공식4.10.0 `SQLiteDatabase.java`/`SQLiteConnection.java`의 읽기 전용 분기를 확인했다.
  읽기 전용 연결은 journal/WAL/페이지 설정의 쓰기 경로를 건너뛰고 locale 처리는 플래그로 끈다.
  로그 pragma의 의미는 [공식 cipher_log_level API](https://www.zetetic.net/sqlcipher/sqlcipher-api/#cipher_log_level)에 근거한다.
- 공개 helper와 라이브러리만 `/data/local/tmp/kakao-cli-profile-helper`에 전송한다.
  DB/WAL/프로필 사본은 기기 내부 임시 경로에도 만들지 않는다.
  새 디렉터리 소유 획득 후 실행하며 기기 해시도 확인한다. 실행 종료/오류/timeout 시
  해당 helper PID만 종료하고 자신이 만든 경로·ART 캐시를 정리한 뒤 경로 부재를 확인한다.
  이미 존재하는 helper 경로는 사용 중으로 간주해 거절하고 삭제하지 않는다.
- 이름은 조회 결과용 메모리 파이프와 `Database` 객체 메모리에만 둔다.
  오류에는 고정 분류만 사용하며 예외 원문은 출력하지 않는다.
  일반 CLI 응답에는 이름/본문이 있으므로 파일로 리다이렉트하지 않는다.
  아래 실기 검증기는 숫자·불리언 허용목록으로만 출력한다.

## 실행 검증

합성 계약을 먼저 작성해 초기5개 실패를 확인하고 구현했다.
신규22개는 fake preference/KDF, 잘못된 protobuf·ID·중복·크기, 공식 배포물/helper 해시 변조,
다운로드 해시 실패 잔여 파일, 엔진 readonly 플래그·복구 거절·로깅 검사,
채널 ID 조인·오픈채팅 별칭·ID fallback, 전용 경로 경합/timeout/정리 실패,
예외 원문 비노출을 검증한다. fixture의 이름·키 자료는 모두 합성이다.

```bash
PYTHONPATH=src uv run --python .venv/bin/python pytest -q --tb=short
uv run --python .venv/bin/python python tests/verify_sqlcipher_live.py
uv build --python .venv/bin/python
git diff --check
```

- 전체 회귀: **158 passed in 57.89s**(기존136 + 신규22).
- 실기 검증: 위 집계 전부 통과. helper 정상 JSON/readonly/메모리 프로필 사용/정리=true,
  stderr=false, 본문·kind·is_mine 기준 일치, 해석 실패0.
- 소스 배포본과 wheel 빌드 성공. 두 압축파일의 리소스·라이선스 포함 및 금지 바이너리0 확인.
- 기존 타입 실기 검증기는 과거 범위가 유지되도록 선택적 SQLCipher 경로만 끄도록 명시했다.

## 운영 경계·남은 점

메시지 전송·UI 조작·로그인·컨테이너/서비스 수정 또는 재시작·운영 배포 파일 변경은 없었다.
VM의 외부 캐시에 공개 엔진/helper를 준비하고 일회성 임시 helper만 실행·정리했다.
ADB 인증 변경과 Mac Lima 실기동은 계속 보류다. 푸시·머지는 수행하지 않는다.

실측 범위는 현재26.8.3/x86_64 한 계정이다. 다른 ABI·업데이트된 스키마·실제 손상/잘못된 키는
실기 시험하지 않았으며 합성 거절 계약만 검증했다. 운영 서비스 배포와 Docker 내부 helper 캐시
준비는 별도 작업으로 남긴다. 이번 완료는 구현·실키 읽기 전용 실증·로컬 커밋까지다.

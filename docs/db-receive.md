# DB 수신 경로

`rooms`, `read`, `poll`의 기본값은 `--source db`다. 기기 내부의
`sqlite3 -readonly -json` 결과와 선택적 기기 내부 SQLCipher 프로필 조회 결과를
`adb exec-out` 표준출력으로 메모리에 받는다.
DB 파일·WAL·사본을 반출하거나 쓰기, VACUUM, 체크포인트를 실행하지 않는다.
복호화 본문과 이름은 요청 응답/화면용 메모리에서만 사용하며 로그/캐시에 쓰지 않는다.
UI 폴백의 dump도 `/dev/tty`로 수신해 파일을 만들지 않는다.
CLI 출력을 파일이나 로그 수집기로 리다이렉트하면 본문이 저장되므로 하지 않는다.

```bash
kakao rooms --source db --json
kakao read me --source db --json
kakao read room:123456 --source db
kakao poll me --source db --interval 3
kakao read me --source ui
```

기본 DB 경로는 화면을 열거나 읽음 처리를 하지 않는다. 앱 동기화 범위의 이력만 읽는다.
`read`의 기존 `messages: string[]`는 그대로 두고 `message_details`를 추가했다.
상세 행은 `text`, `kind`, `type`, `sender`, `user_id`, `is_mine`, `created_at`,
`time`(Unix 초), `unread_count`, `_id`, `id`, `source`를 제공한다.
`poll` 이벤트에는 동일 필드와 `room`이 추가된다. 기존 `room/text` 필드는 유지한다.

방 이름은 자기채팅=`me`, `private_meta.name`, `meta[type=1].content`,
오픈채팅(`OM`/`OD`)의 `open_link.name`, 복호화 가능한 멤버 이름 순서다. 나머지는 `room:<id>`로 명시하며 `rooms`의
`target`을 그대로 사용한다. 동명이면 ID를 사용한다. 방 이름을 DB에서 고유하게
찾지 못하면 경고 후 기존 UI 이름 경로로 폴백한다. UI 이름과 DB ID를 추정해
영구 저장하지 않는다. 현재 앱에는 `friends` 테이블이 없고 일부 프로필은
신규 `crypto_user_database`로 이동했다. 일반 SQLite로 열리지 않는 이 DB는 사용자 승인에 따라
공식 SQLCipher Android 4.10.0으로 기기 내부에서 제자리 `OPEN_READONLY` 조회한다.
아래 준비가 없거나 엔진/키/스키마 조회에 실패하면 기존 이름과 ID fallback을 유지한다.
이 선택적 이름 조회 실패만으로 UI 경로로 전환하지 않는다.
오픈채팅 닉네임은 `KakaoTalk2.db.open_chat_member`에서 현재 계정 ID로 복호화한다.
지원하지 않는 이름은 `user:<id>`로 표시한다.

읽음수는 `chat_rooms.members[i]`와 `watermarks[i]`를 대응시켜,
해당 메시지의 서버 `id`보다 읽은 ID가 작은 멤버 수다. 자신과 발신자는 제외한다.
자기채팅은 0, 배열 불일치·형식 변경·자기 ID 불명은 `null`로 표시한다.
이는 **다른 참가자의 안읽은 수**이며 방의 `unread_count`(내 안읽은 메시지 수)와 다르다.
이미 나간 멤버나 멤버십 변경 시점의 과거 상태까지 복원하는 값은 아니다.

`poll`은 선택 방의 `MAX(_id)`를 시작 커서로 잡아 기존 이력을 재출력하지 않는다.
커서 이후 `_id`를 오름차순으로 조회하고 `--limit` 크기로 나누어 전부 처리하므로
동일 본문의 여러 행이나 한 폴링 간격의 다량 수신을 텍스트 중복으로 버리지 않는다.
DB 초기화/재설치에 따른 `_id` 감소와 기존 행 수정/삭제는 신규 메시지가 아니다.

버전 가드는 실측 `versionCode=29260830`(26.8.3)과 사용 열 존재를 확인한다.
앱 업데이트·스키마 변경·본문 복호화 1건 실패라도 DB 결과를 성공으로 출력하지 않고
한국어 경고 후 UI로 폴백한다. 암호 지원은 enc 0..31, salt 16바이트,
PKCS#12 SHA-1 2회/32바이트 키, AES-256-CBC/PKCS7이다.
본문 키는 각 행의 발신자 ID이며 키 캐시만 최대 2,048개로 제한한다.

콘솔 잠금 중 DB 조회는 허용한다. UI 명시/폴백은 `console_busy`(7)로 거절한다.
TUI 방 목록·수신도 DB 우선이며 송신 직전에 매번 잠금을 확인하고 UI 방을 연다.
이름 없는 ID 방에서는 TUI 송신을 거절한다. UI 폴링도 반복마다 잠금을 확인한다.

## 종류 실측 표 (2026-10-20 후속 5)

26.8.3 APK의 `ChatMessageType.kt`(`tg9`, classes10.dex) enum/플래그와 실기 분포를
대조했다. 원본 `type`은 유지하며 `base_type`, `base_kind`, `is_deleted`,
`is_blinded`, `is_secret`를 추가했다. 확인하지 않은 원본 숫자는 `unknown(n)`으로 남긴다.

| DB type | 행 수 | kind | 1차 enum |
|---|---:|---|---|
| 0 | 15 | system | Feed |
| 1 | 945 | text | Text |
| 2 | 40 | photo | Photo |
| 3 | 1 | video | Video |
| 12 | 12 | sticker | Sticker |
| 18 | 21 | file | File |
| 20 | 14 | animated_sticker | AnimatedSticker |
| 25 | 7 | animated_sticker_ex | AnimatedStickerEx |
| 26 | 35 | reply | Reply |
| 27 | 1 | multi_photo | MultiPhoto |
| 71 | 27 | leverage | Leverage |
| 72 | 14 | alimtalk | Alimtalk |
| 16385 | 1 | deleted (base_type=1) | DELETED_ALL_CHAT_TYPE=0x4000 |
| 16386 | 1 | deleted (base_type=2) | 동일 플래그 |
| 16410 | 1 | deleted (base_type=26) | 동일 플래그 |

실기 합계 1,135행, 해석 실패0, 현재 관측값 중 unknown0이다.
`-11`(DeletedAll), `-13`(OpenLinkIllegalBlind), 양수의 삭제0x4000/가림0x8000/
비밀0x10000000 플래그는 같은 enum에서 확인했다. -11/-13/가림/비밀 조합은
현재 실기 관측이 없어 **합성 테스트로만 검증**했다. 미확정 상위 비트는 일반 타입으로 추정하지 않는다.

삭제·가림은 남아 있는 암호문을 복호화하지 않고 `[Deleted]`/`[Blinded]`로 표시한다.
삭제·가림이 아닌 행은 기존 본문을 유지한다. reply는 답장 본문이며 인용 원문/첨부 확장은
제공하지 않는다. system은 Feed 구분까지만 확인했고 입장/퇴장 등 세부 사건은 추측하지 않는다.

이전 평문 메타 보완은 방 이름14/55 → 17/55였다.
[당시 타입·의존성 대기 기록](verification/2026-10-20-profile-types.md)은 역사적 근거다.
추가 승인 후 SQLCipher 실키 조회로 방 이름17/55 → 55/55, 발신자 이름536/1,135 →
1,025/1,135행을 확인했다. 이는 DB 이름의 ID 조인 검증이며 UI 표시와의 대조는 아니다.

## 선택적 SQLCipher 프로필 준비

소스 체크아웃에서 JDK와 Android SDK의 D8을 준비한 뒤 실행한다.
실측 도구는 javac21.0.12.1 / D8 9.2.4-dev(아래 검증 문서의 빌드 식별자)다.

```bash
uv run --python .venv/bin/python scripts/build-profile-helper.py --d8 /path/to/android-build-tools/d8
```

`--d8` 대신 `KAKAO_D8`도 가능하다. 공식 Zetetic AAR4.10.0과 그 런타임 API 의존
AndroidX SQLite2.2.0만 버전·SHA-256 고정으로 받는다. 공개 배포물과 빌드 helper는
`~/.cache/kakao-cli/sqlcipher-4.10.0/`에만 둔다. Python 의존성 추가는 없다.
helper 재현 빌드가 고정 해시와 다르면 설치하지 않으며 실행도 거절한다.
실행 호스트마다 해당 캐시를 준비해야 한다. Docker 이미지에는 포함하지 않는다.
운영 VM에서는 VM 셸 캐시와 webui 컨테이너 캐시 볼륨 양쪽에 준비돼 있다
([갱신 배포](deploy-compose.md#갱신-배포-2026-10-20-44b44f4-sqlcipher-프로필-반영)).

기기는 기존 root 권한이 필요하며 helper가 `adb root`를 요청하거나 서비스를 재시작하지 않는다.
공개 helper/라이브러리만 `/data/local/tmp/kakao-cli-profile-helper`에 배치하고 기기에서도
해시를 확인한다. 기존 디렉터리가 있으면 경합으로 거절하며 타 실행의 파일을 삭제하지 않는다.
실행 후 자신이 만든 임시 경로와 ART 캐시를 정리하고 부재를 확인한다.
키는 기기 내부에서 지정된 DataStore 두 필드만 해석해 도출하고 native byte[] API에 전달한다.
키를 argv/환경변수/SQL 문자열/출력/파일로 넘기지 않으며 Java·SQLCipher 로그와 core dump를 끈다.
DB·WAL·프로필 사본은 기기 안팎 어디에도 만들지 않는다.

방 이름 우선순위는 기존 메타를 유지한다. PlusChat 채널은 chat_id와 참가자/실제 발신자 ID가
함께 일치하고 후보가 하나일 때만 이름을 채운다. 일반 멤버 이름은 friend/contact/nickname
우선순위를 사용하며 오픈채팅의 방별 별칭은 전역 프로필로 덮어쓰지 않는다.
확인되지 않은 발신자110행은 계속 ID를 사용한다.
[고정 해시·읽기 전용 계약·실기 검증·한계](verification/2026-10-20-profile-sqlcipher.md)를 참고한다.

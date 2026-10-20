# Hermes 검토 요청: 새 프로필 DB SQLCipher 의존성

기준: codex-db / 989301ae5c47ab332762de0c68a3acfd87c64342.

- 실기 crypto_user_database / crypto_database 모두 기기 내 sqlite3 -readonly의
  sqlite_master 조회에서 file is not a database 오류. 일반 SQLite로 읽을 수 없음.
- adb exec-out은 원격 오류를 stdout으로 반환하고 종료 코드0을 줄 수 있었다.
  종료 코드만으로 성공을 판단하지 않고 JSON 파싱과 양쪽 출력의 오류 여부로 확인했다.
- 내장 sqlite3의 PRAGMA cipher_version 결과는 없음(SQLCipher 기능 없음).
- 1차 구현 rokrokss/kakaotalk-bridge @097a9b8 iris/CryptoProfiles.kt는
  net.zetetic.database.sqlcipher.SQLiteDatabase로 crypto_user_database를 OPEN_READONLY로 열고
  user / talk_channel을 조회한다. 런타임 키 값과 자격증명 파일은 읽지 않았다.
- 따라서 프로필 조회에는 **기기 내부에서 실행할 SQLCipher 4 호환 읽기 전용 엔진**이
  필요하다. 호스트 Python SQLCipher 패키지는 DB 사본 반출을 요구하므로 해결책이 아니다.
- 요청 의존성: Android ABI에 맞는 SQLCipher CLI 또는 읽기 전용 전용 helper.
  버전·출처·해시·라이선스 및 비파괴 오류 처리(손상 시 삭제 금지), 키는 기기 내부
  메모리에서만 처리하고 argv/환경/로그/stdout에 노출하지 않는 설계의 별도 검토가 필요하다.
- 승인 전에는 다운로드/빌드/기기 배치/프로필 키 접근/새 암호화 DB 복호화를 하지 않는다.
  기존 cryptography 외 새 의존성을 추가하지 않았다.
- 다른 단계(합성 테스트, 확정 타입 매핑, 기존 평문 프로필 메타 개선, 문서)는 계속한다.

기존 SQLCipher 여부 판단은 일반 SQLite 거절 + 1차 구현 근거다.
정확한 암호 파라미터·실제 키로 열린다는 실증은 승인된 엔진 도입 전까지 미완료다.

KAKAO-PROFILE-DEPENDENCY SQLCipher4-Android 읽기전용 엔진 필요, 키·DB 기기 외 반출 금지

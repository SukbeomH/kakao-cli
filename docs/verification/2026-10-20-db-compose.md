# DB 수신·compose 검증 (2026-10-20)

## D 실행 결과

- 대상: VM `agent@192.168.0.30`, redroid, 앱 26.8.3 / versionCode 29260830.
- `tests/verify_db_live.py --ui`를 VM에서 실행. DB·WAL·사본 반출 없음.
- 전체 chat_logs 1,134행 중 본문 있는 1,105행: **1,105 성공 / 0 실패 (100%)**.
- chat_rooms 55개, 복호화 프로필 이름 캐시 45개, 이름이 확인된 방 14개.
- 자기채팅 최근 20개: 전부 is_mine=true.
- 자기채팅 UI 텍스트 9개: DB 최근 이력과 순서/본문 일치=true,
  UI is_mine 전부 true, DB 자기채팅 is_mine 전부 true.
- 마지막 MAX(_id) 이후 0건=true, 직전 _id 커서에서 마지막 행 검출=true.
  폰 송신을 요청하지 않았으며 새 메시지를 전송하지 않았다.
- 실제 기기 잠금 파일을 빈 자리에만 임시 생성하여 검증:
  잠금 활성=true, `cmd_read(source=db)` 성공=true, UI `console_busy` 거절=true.
  소유 토큰을 확인한 뒤 검증용 잠금만 제거했다. 기존 콘솔 잠금을 덮어쓰지 않았다.
- 초기 합성 잠금 + 실제 DB transport 검증도 두 항목 true.
- 단위/기존 회귀: `PYTHONPATH=src uv run --python .venv/bin/python pytest -q`
  **94 passed** (16.47초), `git diff --check` 통과.
- 검증 중 발견/해결: 시스템 Python 3.10 요구 버전 불일치 → 프로젝트 .venv Python 3.11,
  가짜 salt 테스트의 패딩 길이 오기 → 16바이트 수정,
  음수 room id 존재 → 부호 있는 64비트 SQL 정수 검증,
  TUI 진입 잠금 테스트 → DB 초기화/폴백 경계를 진입 시 적용.
- 복호화 본문·이름·ID 원본을 이 문서/로그/fixture에 저장하지 않았다.
  UI dump는 `/dev/tty` 표준출력으로 메모리에서만 처리했다.
- 변경 Python 모듈과 검증 스크립트만 VM에 scp 배포했다.
  상시 `kwebui` 세션은 유지, 재시작 없음. redroid 컨테이너 변경 없음.

## 한계

- 현재 앱의 friends 테이블은 없음. SQLCipher 프로필은 범위 밖이며 이름 미확인 방은
  ID로 선택하거나 기존 UI 이름 경로로 폴백한다. 이름 유사성에 의한 추정 매핑 없음.
- 종류는 type 1/2/3만 text/photo/video로 확정하고 나머지는 unknown(n).
- 새 메시지 도착 실험은 금지 범위이므로 기존 마지막 _id 경계로 검증했다.
- 실측 Linux/현재 앱 증거이며 다른 버전, Mac 기동/로그인 인증은 아니다.

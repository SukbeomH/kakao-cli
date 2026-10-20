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

## D 보강 후 재검증

- 시작 SHA: `45c9edb1c67c82aeab18da910c93f544404ec8c1`.
- D 최종 커밋: `c128ea8577e9aabc5c7f35fd5390ee278a7c10f4`.
- TUI의 DB 방 ID → UI 이름 전환은 이름이 고유하고 같은 ID로 역매핑될 때만 허용한다.
  동명/이름 불명은 송신 전에 거절한다. 실제 송신 실험은 하지 않았다.
- JSON 내부 객체/배열 형식 변경도 DBUnavailable로 UI 폴백하도록 보강했다.
- 공통 Device.dump도 파일을 만들지 않는 `/dev/tty` 수신으로 전환했다.
  UI 폴백은 기존 chat_log 대기/오류 계약을 유지한다.
- 해당 보강 후 동일 VM 실기 검증 재실행: 1,134행 / 본문1,105 / 성공1,105 /
  실패0, 자기채팅 UI9개 일치=true, 최근20 isMine=true, 증분 경계 true,
  실제 잠금 중 DB 성공=true/UI 거절=true. 본문/이름을 출력하지 않았다.
- 최종 전체 테스트: **104 passed (16.77초)**. C의 가짜 배포 테스트 4개 포함.

## C 정적 실행 결과

- Linux 이미지 digest는 현재 컨테이너의 image inspect에서 확인했다.
  Docker Hub registry manifest에서도 해당 digest 일치와 amd64/arm64를 확인했다.
- Lima 64only digest도 registry manifest digest 일치와 arm64 지원을 확인했다.
  이미지를 pull하거나 컨테이너를 생성하지 않았다.
- VM Docker Compose **v5.6.0**에서 `COMPOSE_DISABLE_ENV_FILE=1`로 실행:
  - Linux compose `config --format json` 통과.
  - Linux + Lima overlay `config --format json` 통과.
  - 3개 서비스, adb/web loopback, 고정 digest, 기존 /data bind,
    binderfs 노드 개별 bind/create_host_path=false, ro.adb.secure=0,
    use_memfd=1, host network 없음, boot 완료 의존성: 전부 true.
  - Lima overlay의 linux/arm64 + 64only=true.
  - 기존 redroid 실행 인자 10개가 전부 새 구성에 보존됨=true.
  - secure=1 옵션과 명시적 LAN 웹 bind 옵션의 config 결과도 각각 일치=true.
- VM 기존 PyYAML **6.0.1**로 Lima YAML 파싱 통과.
  vz/arm64/mounts 없음/웹 loopback 단일 포워딩/나머지 무시=true.
- Lima provision의 `bash -n` 통과. 내장 binder 스크립트/유닛이 독립 파일과 동일=true.
- 새 셸 스크립트 4개 `bash -n` 전부 통과.
- `systemd-analyze verify`로 binder unit 문법 통과. 검증 시 ExecStart만 임시 검토
  디렉터리의 동일 스크립트로 치환해 경로 존재를 확인했으며 **실행/설치하지 않았다**.
- `shellcheck`, `limactl`은 호스트·VM에 없음: 해당 도구 검사는 미실행.
  Lima 검증은 YAML 파싱/스크립트 문법까지이며 실제 VM 기동 증거가 아니다.
- 가짜 limactl 테스트: 기본 config --quiet, 자동 up/start 없음,
  Git 추적 코드만 전달, down -v는 게스트 접근 전에 거절.
- 가짜 adb 테스트: APK 부재 시 입력/앱 실행 전에 실패, 정상 boot의 유일한 input은 HOME.
- `uv build --python .venv/bin/python` 성공: sdist/wheel 생성.
  sdist의 compose/Lima/래퍼/NOTICE 포함, .env·DB·키 확장자 없음=true.
  wheel의 kakaodb/NOTICE 포함=true. 컨테이너 이미지 build/실배포는 하지 않았다.

## 사용자 결정 필요 사항과 보류 기본값

1. Linux 현행 systemd → compose 실제 전환: **보류**. 같은 /data bind·기존 컨테이너 보존
   절차와 rollback은 `docs/deploy-compose.md`에 기록했다.
2. Apple Silicon Lima VM 생성·카카오 로그인: **보류**. 새 데이터 사용,
   기존 태블릿 슬롯/세션 해제 가능성은 사용자 판단 사항이다.
3. ADB 인증 강제: **off 유지**. 공개키 사전 등록 후 secure=1 적용 절차만 문서화했다.
4. 웹 LAN 공개: **loopback 유지**. KAKAO_WEB_BIND 명시 옵션만 제공한다.
5. SQLCipher 프로필과 미확정 메시지 타입: **후속**. 키 파일을 읽지 않았으며
   현재는 명시 ID/unknown(n)/UI 폴백으로 처리한다.

배포 파일 검토용 사본은 VM `/tmp/kakao-compose-codex.xLmDbs`에 있다.
그 디렉터리에는 코드·구성만 있으며 DB/본문/이름/자격증명은 없다.

## 종료 상태 확인

- VM에 배포한 변경 Python 모듈7개의 SHA-256이 로컬과 모두 일치=true.
- redroid StartedAt가 시작 관측값과 동일=true, RestartCount=0=true.
- 실행 redroid 컨테이너 수=1, 기존 tmux kwebui 세션 존재=true.
- 전송/DB 반출/복호화 본문·이름 기록/라이브 redroid 재생성·재시작/푸시를 하지 않았다.

- 최종 SHA 범위 공백 검사에서 Lima YAML 빈 줄3곳의 후행 공백을 발견해 제거했다.
  RTK 축약 출력 대신 원본 git diff --check로 전체 시작 SHA 범위를 재검증했다.

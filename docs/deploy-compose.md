# Linux compose / Apple Silicon Lima

현재 단계는 **파일 작성과 정적 검증만** 완료했다. 라이브 VM에서는 compose up,
redroid 재생성/재시작, 포트 변경, 두 번째 redroid 실행을 하지 않았다.
아래 기동·전환 명령은 사용자가 전환을 승인한 뒤 신규/정지한 호스트에서 실행하는 절차다.
**`docker compose down -v` 금지**: 키·콘솔 파일·캐시 볼륨을 지운다. 다른 구성에서
Android 데이터를 명명 볼륨으로 바꿨다면 로그인과 DB까지 삭제된다. 정지는 `stop`을 쓴다.

## 구성과 기본값

| 항목 | 기본값 |
|---|---|
| Linux redroid | 현행 VM 이미지 digest `sha256:a3e654caad74faed03cd5ead057a989e06f507778cda678e5d3b9772e0092f1c` |
| Lima redroid | 14.0.0_64only digest `sha256:0a611199ba2e0b5d60af39b3327a517f6407231f4352114ed3bd3cbfe2be69aa` |
| Android /data | `/opt/redroid/data` bind, 없으면 자동 생성하지 않고 거절 |
| 태블릿 지문 | 현행 인자 전부 유지: SM-X710/gts9wifi, 1600×2560/340, GPU guest |
| memfd | `androidboot.use_memfd=1` (ashmem 대체, 현행 실행 인자에만 추가) |
| ADB 공개 | 호스트 `127.0.0.1:5555`만, 서비스 내부 `redroid:5555` |
| ADB 인증 | `KAKAO_ADB_SECURE=0`, 사용자 선택 후 1 |
| 웹 공개 | 호스트 `127.0.0.1:8800`, 명시적 `KAKAO_WEB_BIND`로만 변경 |
| 웹 파일 루트 | 명명 볼륨 `console-files` → `/files`, 호스트 홈 공유 없음 |
| 키/캐시 | `adb-state` → `/root/.android`, `scrcpy-cache` → `/root/.cache/kakao-cli` |
| 부팅 | redroid healthy → boot 1회성 앱/권한/IME 준비 → webui |

redroid는 privileged 컨테이너이며 전용 Linux 호스트를 사용한다.
컨테이너 내부 웹 바인드는 Docker 포워딩을 위해 0.0.0.0이지만 호스트는 loopback이다.
`--allow-screen`은 컨테이너 내부에서 콘솔을 활성화하는 명시 옵션이다.
boot는 APK를 내려받거나 계정 로그인을 자동화하지 않는다. 공식 카카오톡과 ADBKeyBoard가
없으면 실패하므로 사용자가 APK를 설치한 다음 boot를 다시 실행해야 한다.

## Linux 신규 호스트

Ubuntu 24.04, 4K 페이지 커널, Docker Engine/Compose v2 이상, Python 3가 필요하다.
Docker Desktop/OrbStack의 내장 커널을 사용하는 절차가 아니다.

```bash
# 승인한 신규 호스트에서만. 기존 라이브 VM에 그대로 실행하지 않는다.
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 python3 adb "linux-modules-extra-$(uname -r)"
sudo scripts/install-binder.sh
sudo systemctl start kakao-binder.service
sudo mkdir -p /opt/redroid/data     # 신규 데이터 경로만. 기존 경로는 그대로 사용

# 비밀 파일을 읽지 않도록 Compose의 자동 .env 로드를 비활성화한다.
export COMPOSE_DISABLE_ENV_FILE=1
docker compose -f deploy/compose.yaml config --quiet
# 승인된 신규 환경의 Android만 먼저 기동, 공식 APK/ADBKeyBoard 설치 후 계속한다.
docker compose -f deploy/compose.yaml up -d redroid
adb connect 127.0.0.1:5555
# 사용자 보유 공식 APK split 및 ADBKeyBoard를 adb로 설치한다.
docker compose -f deploy/compose.yaml up -d --build
```

`prepare-binder.sh`는 binder 모듈·binderfs를 준비하고, 필요한 경우 Linux UAPI의
BINDER_CTL_ADD ioctl로 binder/hwbinder/vndbinder 노드를 만든다. 노드는 개별 bind한다.
`kakao-binder.service`는 `Before=docker.service`이며 Docker drop-in도
`Requires/After=kakao-binder.service`를 둬서 노드 생성 실패 시 Docker 기동을 막는다.
설치 스크립트는 Docker를 재시작하지 않는다. 호스트 준비 스크립트는 이번에 실행하지 않았다.

Compose restart 정책으로 redroid/webui는 호스트 재부팅 뒤 다시 실행된다. 1회성 boot를
재실행해야 하는 경우 `docker compose -f deploy/compose.yaml run --rm boot`를 사용한다.
부팅 스크립트는 카카오톡을 실행·권한·IME 설정 후 HOME으로 보내며 전송은 하지 않는다.

접속은 `http://127.0.0.1:8800`, 원격 VM은 SSH 터널을 사용한다.
LAN 공개가 필요하면 승인 후 다음과 같이 **명시적으로** 지정한다(무인증 콘솔):

```bash
COMPOSE_DISABLE_ENV_FILE=1 KAKAO_WEB_BIND=192.168.0.30 \
  docker compose -f deploy/compose.yaml up -d webui
```

## Apple Silicon Mac

Lima 2.0+를 준비한다. Docker Desktop 컨텍스트는 바꾸지 않는다.
`lima.yaml`은 VZ, Ubuntu 24.04 arm64, 6 CPU/8GiB/40GiB, **mounts=[]**다.
Mac 홈·프로젝트·SSH agent를 게스트에 공유하지 않으며, 웹 8800 하나만 Mac loopback으로
포워딩하고 나머지 포트는 무시한다. ADB 5555는 Mac으로 포워딩하지 않는다.

```bash
# 실제 VM 생성은 별도 승인 뒤에만 실행한다.
limactl validate deploy/lima.yaml
limactl start --name=kakao deploy/lima.yaml
scripts/lima-compose.sh config --quiet
scripts/lima-compose.sh up -d redroid
# 게스트에서 adb로 공식 APK/ADBKeyBoard 설치; .env/키/기존 DB는 복사하지 않는다.
# 필요하면 limactl copy로 사용자가 지정한 APK만 전달한다.
scripts/lima-compose.sh up -d --build
# 브라우저 http://127.0.0.1:8800
scripts/lima-compose.sh stop
```

래퍼 기본 명령은 `config --quiet`다. VM 자동 생성이나 자동 up을 하지 않는다.
Git 추적 코드만 tar 스트림으로 `/opt/kakao-cli`에 전달한다(커밋 후 실행).
DB·.env·키 확장자는 추적돼 있어도 제외한다. `--workdir`는 게스트 절대 경로이며,
Mac의 Docker CLI/컨텍스트를 쓰지 않고 `limactl shell … sudo docker compose`로 실행한다.
`KAKAO_LIMA_INSTANCE`로 다른 Lima 인스턴스를 선택할 수 있다.

ARM VM은 x86 VM의 /data를 가져오지 않고 새 데이터로 로그인한다.
아키텍처/키스토어/기기 바인딩 차이 때문에 데이터 이전을 보장할 수 없다.
카카오 태블릿 슬롯이 하나라고 가정하므로 **Mac 로그인 시 기존 VM 세션 해제 가능성**을
사용자가 판단해야 한다. Mac 실제 부팅·로그인은 이번 검증 범위 밖이다.

## VM 108 전환 완료 (2026-10-20, Hermes)

- 실행: `~/kakao-compose`(git archive 사본)에서
  `COMPOSE_DISABLE_ENV_FILE=1 docker compose -f deploy/compose.yaml -f deploy/compose.live.yaml up -d`.
- `deploy/compose.live.yaml`(VM 전용 오버레이): 전환 전과 **런타임을 동일하게** 유지 — redroid 인자는
  기존 10개 그대로(`use_memfd` 미추가), binderfs 노드 개별 bind 없음(기존처럼 privileged redroid가
  자체 할당), `/data`=`/opt/redroid/data`. 웹 콘솔만 `192.168.0.30:8800`(LAN, 무인증)으로 공개.
- 호스트: `scripts/install-binder.sh` 설치·`kakao-binder.service` enable/start(부팅 시 binder 준비, docker 앞).
  `kakao-redroid.service`는 `disable --now`. 기존 컨테이너 `redroid`는 **삭제하지 않고 정지 보관**(롤백용).
- 결과: adb `127.0.0.1:5555`만(LAN에서 5555 접속 불가 확인 — 이전 0.0.0.0 노출 해소), 웹 `192.168.0.30:8800` 200,
  로그인 유지(MainActivity, chat_logs 행 증가 지속), 웹 콘솔 H.264 MSE 가상 928×744 ~16fps.
- 발견: 전환 전 adbd 가 root 였던 것은 과거 수동 `adb root` 덕분(재부팅 시 초기화) — 새 컨테이너에선 shell 이라
  DB 수신이 열리지 않았다. `compose-boot.sh` 가 부팅마다 `adb root` 를 켜도록 수정.
- 호스트 CLI(VM 셸)는 부팅 후 `adb connect 127.0.0.1:5555`(또는 `--connect`)가 필요하다(예전엔 kakao-boot.sh 가 연결).
- 롤백: 아래 5번 절차 그대로(`compose stop` → `systemctl enable --now kakao-redroid.service` → 기존 kwebui 명령).

### 갱신 배포 (2026-10-20 `44b44f4`, SQLCipher 프로필 반영)

```bash
# 제어 호스트 저장소에서. 이전 배포본은 ~/kakao-compose.prev 로 보관.
git archive --format=tar --prefix=kakao-compose/ HEAD | ssh agent@192.168.0.30 \
  'rm -rf ~/kakao-compose.new && mkdir ~/kakao-compose.new && tar -x -C ~/kakao-compose.new && \
   rm -rf ~/kakao-compose.prev && mv ~/kakao-compose ~/kakao-compose.prev && \
   mv ~/kakao-compose.new/kakao-compose ~/kakao-compose && rmdir ~/kakao-compose.new'
ssh agent@192.168.0.30 'cd ~/kakao-compose && export COMPOSE_DISABLE_ENV_FILE=1 && \
  F="-f deploy/compose.yaml -f deploy/compose.live.yaml" && \
  docker compose $F build webui && docker compose $F up -d --no-deps webui'   # redroid 무중단
```

- SQLCipher 엔진·helper(공개 배포물, 해시 고정)는 이미지에 넣지 않는다. VM 캐시
  `~/.cache/kakao-cli/sqlcipher-4.10.0/`를 webui 컨테이너의 캐시 볼륨에 한 번 복사했다:
  `docker cp ~/.cache/kakao-cli/sqlcipher-4.10.0 kakao-webui-1:/root/.cache/kakao-cli/`
  (명명 볼륨이라 재빌드·재시작에도 유지. `down -v`를 하면 다시 복사해야 한다.)
- VM 셸 CLI(`~/kakao-cli/src`)도 같은 커밋으로 동기화(`rsync -a --delete src/kakao_cli/ …`).
- 컨테이너 안 CLI는 `--serial redroid:5555`, VM 셸 CLI는 `--serial 127.0.0.1:5555`.
- 확인: 웹 200, 영상 ~17fps, `rooms` DB 55개 중 이름 55개(VM 셸·컨테이너 모두), 기기 임시 helper 잔여 0.

## 현행 systemd에서 전환/롤백 (원 절차)

현행 `kakao-redroid.service`는 `kakao-boot.sh`로 기존 `redroid`를 시작한다.
compose와 동시에 기동하면 같은 데이터/포트를 쓸 수 있으므로 소유자를 하나로 정한다.
`run-redroid.sh`는 기존 컨테이너를 삭제하는 명령이 있어 전환에 사용하지 않는다.

1. 사용자가 유지보수 시간을 승인한 뒤 현행 이미지 digest/인자/볼륨과 웹 콘솔 기동
   명령을 보관한다. 본문/이름/DB/키 파일은 수집하거나 복사하지 않는다.
2. 기존 `kwebui`의 기동 명령을 확인해 재기동할 수 있게 한 뒤 세션을 종료한다.
   `sudo systemctl disable --now kakao-redroid.service`로 기존 Android를 정지한다.
   기존 컨테이너는 삭제하지 않고 보관한다. 다른 redroid가 실행 중인지 확인한다.
3. 호스트 binder 준비를 설치하고, **기존 `/opt/redroid/data`를 그대로 bind**한다.
   DB 사본을 만들거나 다른 호스트로 반출하지 않는다. compose config에서 source를 확인한다.
4. compose를 기동한다. 동일 호스트·동일 Android 이미지·동일 /data이므로 기존 로그인
   유지가 예상되지만 보장하지 않는다. 새 로그인이나 세션 해제가 나타나면 중지하고 판단한다.
   기존 호스트 kwebui와 compose webui가 동시에 8800을 잡지 않게 한다.
5. 롤백: compose를 `stop`하여 /data 사용을 완전히 멈춘 뒤 기존 서비스를
   `sudo systemctl enable --now kakao-redroid.service`로 되돌린다.
   저장한 **동일 명령**으로 기존 kwebui를 재기동한다. 기존 컨테이너와 /data는 남아 있으므로
   복사/복구 작업 없이 재사용한다. DB를 동시에 여는 두 Android를 띄우지 않는다.

## ADB 인증 옵션 (기본 off, 미전환)

`ro.adb.secure=1`은 사용자 결정 사항이다. 이번에는 키 파일을 읽거나 출력하지 않았다.
인증을 켜기 전에 다음 절차를 사용자 관리 환경에서 수행한다.

1. boot/webui가 공유하는 `adb-state` 볼륨의 ADB 키 쌍을 준비한다.
   개인키는 볼륨 밖으로 내보내거나 로그/저장소에 넣지 않는다.
2. **공개키만** 사용자가 Android `/data/misc/adb/adb_keys`에 등록한다.
   기존 허용키를 덮어쓰지 않고 소유자(system:shell), 권한(0640), SELinux context를
   해당 이미지 기준으로 확인한다. 키 본문을 터미널/문서에 출력하지 않는다.
3. 유지보수 승인 후 `KAKAO_ADB_SECURE=1`로 구성을 재생성한다. boot/webui 모두 같은
   adb-state를 사용한다. 연결 성공 및 `getprop ro.adb.secure`를 확인한다.
4. 인증 실패 시 새 키 생성·계정 재로그인을 반복하지 말고 중지한다. 승인된 롤백 절차로
   기존 구성에 돌아간다. 이미지별 인증 동작과 키 등록은 아직 실측하지 않았다.

## 정적 검증과 출처

실행 결과와 제한은 [검증 기록](verification/2026-10-20-db-compose.md)에 있다.
다음 명령은 컨테이너를 만들지 않는다.

```bash
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f deploy/compose.yaml config --quiet
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f deploy/compose.yaml -f deploy/compose.lima.yaml config --quiet
bash -n scripts/compose-boot.sh scripts/prepare-binder.sh scripts/install-binder.sh scripts/lima-compose.sh
# 설치돼 있으면 shellcheck 위 네 스크립트, limactl validate deploy/lima.yaml
```

- [kakaotalk-bridge @097a9b8](https://github.com/rokrokss/kakaotalk-bridge/tree/097a9b8)
  (MIT): Lima/compose/binderfs 구성 참고, 저작권·라이선스는 NOTICE에 포함.
- [redroid 공식 설정](https://github.com/remote-android/redroid-doc#configuration): memfd 인자.
- [Linux binderfs UAPI](https://github.com/torvalds/linux/blob/master/include/uapi/linux/android/binderfs.h):
  binderfs 노드 할당 ioctl 형식.
- [Lima shell 명령](https://lima-vm.io/docs/reference/limactl_shell/): 게스트 절대 작업 경로 실행.

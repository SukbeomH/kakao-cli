# Linux compose / Apple Silicon Lima

초기 검증은 **파일 작성과 정적 검증만** 진행했다. 이후 Linux 운영 전환은 아래 기록을,
2026-10-21 Apple Silicon 독립 기동은 "Mac 독립 기동 확인"을 참고한다.
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

릴리즈 설치 패키지 또는 저장소 루트에서 다음 설치기를 사용할 수 있다.
아래 수동 절차를 자동화하며, 기존 VM과 이미 설치된 앱을 유지한다.

```bash
brew install lima
bash scripts/setup-macos.sh
```

설치기는 게스트 Python으로 APK를 내려받고, 필요하면 게스트에 apksigner·Java를 설치해
해시와 서명을 확인한다. Mac에는 Lima만 필요하다. 신규 설치기 전체 흐름은 모의 테스트로,
실제 독립 기동·화면은 아래 수동 절차로 확인했다. 기존 `kakao` VM의 커널·binder 준비가
정상이라는 전제이며 템플릿을 이미 생성된 VM에 덮어쓰지 않는다.

맥 자체에서 독립 실행하려면 Lima 안에 Android용 Linux VM을 만든다.
Docker Desktop·OrbStack 컨테이너에 그대로 띄우는 방식은 아니다.
**Mac에 Python·uv·adb·Docker CLI를 따로 설치하지 않아도** 아래 컨테이너 경로를 사용할 수 있다.
APK 서명 검사에 쓸 Android SDK Build Tools의 `apksigner`는 별도로 준비한다(Java 필요).

확인한 구성은 Lima 2.2.1 / VZ / Ubuntu 24.04 arm64 / **CPU 2개·메모리 4GiB·디스크 20GiB**다.
게스트에서는 CPU 2개·메모리 약 3.8GiB·루트 파일시스템 약 19GiB로 보였다.
설치 후 루트 사용량은 약 5.8GiB였다. 장시간 운용·송수신 성능의 최소 사양을 검증한 것은 아니다.

`deploy/lima.yaml`은 **mounts=[]**로 Mac 홈·프로젝트·SSH agent를 공유하지 않는다.
웹 8800을 Mac loopback으로 전달하며 ADB 5555는 Mac으로 전달하지 않는다.
Docker는 게스트 안에서 실행하므로 Mac의 Docker 컨텍스트를 바꾸지 않는다.

### 1. VM 생성과 Android 부팅

아래 명령은 저장소 루트에서 실행한다. `kakao`라는 기존 인스턴스가 있으면 먼저
`limactl list`로 확인하고, 처음 만드는 경우에만 템플릿 경로를 지정한다.

```bash
brew install lima
limactl validate deploy/lima.yaml
limactl start --name=kakao --tty=false deploy/lima.yaml
scripts/lima-compose.sh config --quiet
scripts/lima-compose.sh up -d redroid

# Android 부팅 확인. sys.boot_completed가 1이 될 때까지 기다린다.
limactl shell --workdir / kakao adb connect 127.0.0.1:5555
limactl shell --workdir / kakao adb -s 127.0.0.1:5555 shell getprop sys.boot_completed
scripts/lima-compose.sh ps -a
```

첫 생성은 Ubuntu 이미지 다운로드와 Docker·binder 모듈 설치가 있어 시간이 걸린다.
`limactl start`의 READY와 redroid의 healthy를 확인한다. 부팅 도중 ADB가 잠시 offline인 것은
가능하지만, 계속 offline이면 아래 문제 해결 절차로 로그를 확인한다.

### 2. 카카오톡·ADBKeyBoard APK 준비

Play에서 받은 본인 APK를 사용할 수 있다. 이번 확인에서는
[APKPure의 카카오톡 26.8.3 ARM64 XAPK](https://apkpure.net/kakaotalk-messenger/com.kakao.talk/download/26.8.3)와
[ADBKeyBoard 원저장소 APK](https://github.com/senzhk/ADBKeyBoard/blob/master/ADBKeyboard.apk)를 사용했다.
APKPure는 카카오 공식 배포 사이트가 아니다. 아래는 **확인한 파일을 고정해서 재현하는 예**이며,
다른 버전·다른 split 구성이면 파일명과 해시를 다시 확인한다.

```bash
TASK_APK_DIR="$(mktemp -d /tmp/kakao-apks.XXXXXX)"
curl -fL --retry 2 'https://d.apkpure.net/b/XAPK/com.kakao.talk?versionCode=29260830' \
  -o "$TASK_APK_DIR/kakaotalk-26.8.3.xapk"
curl -fL --retry 2 https://raw.githubusercontent.com/senzhk/ADBKeyBoard/d7b27288bc8c1c8348a79ea6896bd7352c30a9fc/ADBKeyboard.apk \
  -o "$TASK_APK_DIR/ADBKeyboard.apk"

# 2026-10-21에 확인한 파일과 같은지 검사. 실패하면 설치하지 않고 배포물 변경을 확인한다.
TASK_XAPK_SHA=f9703d584ff726c1365b6ca2683ed5b88dbb085fc994cd5af5292ed59189b189
TASK_IME_SHA=e698adea5633135a067b038f9a0cf41baa4de09888713a81593fb2b9682cdc59
printf '%s  %s\n' "$TASK_XAPK_SHA" "$TASK_APK_DIR/kakaotalk-26.8.3.xapk" \
  "$TASK_IME_SHA" "$TASK_APK_DIR/ADBKeyboard.apk" | shasum -a 256 -c -

unzip "$TASK_APK_DIR/kakaotalk-26.8.3.xapk" \
  com.kakao.talk.apk config.arm64_v8a.apk config.en.apk config.mdpi.apk \
  -d "$TASK_APK_DIR"

# apksigner가 PATH에 없으면 Android SDK Build Tools의 절대 경로를 지정한다.
(
  set -e
  for apk in com.kakao.talk.apk config.arm64_v8a.apk config.en.apk config.mdpi.apk; do
    apksigner verify --print-certs "$TASK_APK_DIR/$apk"
  done
)
```

네 카카오톡 APK 모두 서명 검증 성공과 인증서 SHA-256
`2b06cc3d47782d7c497c07f17cb5f859cd6bbcb66829f3e67b96b7a44820d2ce`를 확인한 뒤 설치한다.
SHA-1은 배포 페이지의 `ecc45b902ac1e83c8be1758a257e67492de37456`와 일치했다.
파일 해시는 그 다운로드 파일의 동일성 확인용이며, 그 자체로 배포자를 인증하지 않는다.
ADBKeyBoard 원저장소 APK는 Android Debug 인증서로 서명되어 있다.

### 3. 게스트에 APK 설치 후 웹 콘솔 기동

위의 `TASK_APK_DIR` 변수를 설정한 같은 터미널에서 실행한다.
Mac 홈은 공유하지 않으므로 APK를 `limactl copy`로 전달해야 한다.

```bash
limactl shell --workdir / kakao mkdir -p /tmp/kakao-apks
limactl copy "$TASK_APK_DIR/com.kakao.talk.apk" \
  "$TASK_APK_DIR/config.arm64_v8a.apk" "$TASK_APK_DIR/config.en.apk" \
  "$TASK_APK_DIR/config.mdpi.apk" "$TASK_APK_DIR/ADBKeyboard.apk" \
  kakao:/tmp/kakao-apks/
limactl shell --workdir / kakao adb connect 127.0.0.1:5555
limactl shell --workdir / kakao adb -s 127.0.0.1:5555 install-multiple \
  /tmp/kakao-apks/com.kakao.talk.apk /tmp/kakao-apks/config.arm64_v8a.apk \
  /tmp/kakao-apks/config.en.apk /tmp/kakao-apks/config.mdpi.apk
limactl shell --workdir / kakao adb -s 127.0.0.1:5555 install /tmp/kakao-apks/ADBKeyboard.apk
scripts/lima-compose.sh up -d --build
scripts/lima-compose.sh ps -a

# 정상 상태: redroid healthy / boot Exited (0) / webui Up
curl -fsS http://127.0.0.1:8800/api/state
limactl shell --workdir / kakao sudo docker exec kakao-webui-1 \
  python -m kakao_cli doctor --connect redroid:5555 --serial redroid:5555 --json
```

`api/state`의 `status`가 `ready`, doctor의 `ok`가 true인지 확인한다.
브라우저에서 **http://127.0.0.1:8800**을 열고 `Use with Main Device`(다른 기기와 함께 사용)를
선택한 상태로 `QR Code Log In`을 누른다. 폰 카카오톡에서 QR 스캔과 인증을 직접 진행한다.
boot 스크립트는 앱 실행·권한·입력기 설정만 하며 로그인이나 메시지 전송은 하지 않는다.

### 4. 재실행·상태 확인·종료

```bash
# 이미 생성한 VM 재실행. APK 재설치 없이 기존 /data를 사용한다.
limactl start kakao
scripts/lima-compose.sh up -d redroid
# Android 재부팅 시 adb root가 초기화되므로 boot를 명시적으로 다시 실행한다.
scripts/lima-compose.sh run --rm boot
scripts/lima-compose.sh up -d webui

# 상태 / 로그 / TUI
scripts/lima-compose.sh ps -a
scripts/lima-compose.sh logs --tail 30 boot webui
limactl shell --workdir / kakao sudo docker exec -it kakao-webui-1 \
  python -m kakao_cli tui --serial redroid:5555

# 컨테이너만 종료 (VM은 계속 실행)
scripts/lima-compose.sh stop
# VM까지 종료 (Android 데이터·이미지·볼륨 보존)
limactl stop kakao
limactl list
```

종료 후 `kakao Stopped`인지 확인한다. `limactl delete`나 `docker compose down -v`는
일상 종료에 사용하지 않는다. TUI에서 송신할 때는 웹 콘솔 탭을 닫거나 미러 모드로 바꿔
가상 디스플레이의 송신 잠금을 해제한다.

래퍼 기본 명령은 `config --quiet`이며 VM을 자동 생성하거나 시작하지 않는다.
Git 추적 **경로의 현재 파일 내용**을 tar로 `/opt/kakao-cli`에 전달한다. 추적 파일의 미커밋 수정은
반영되지만 새 미추적 파일은 전달되지 않는다. DB·.env·키 확장자는 추적돼 있어도 제외한다.
`--workdir /` 또는 게스트 절대 경로를 지정해야 Mac 작업 경로를 게스트에서 찾는 오류를 피할 수 있다.
`KAKAO_LIMA_INSTANCE`로 다른 인스턴스를 선택하면 위의 직접 `limactl` 명령에도 같은 이름을 사용한다.
템플릿의 CPU·메모리·디스크·provision 변경은 이미 생성된 VM에 자동 적용되지 않는다.

### 문제 해결: 실제 독립 기동에서 확인한 항목

- **binder Permission denied / Android 서비스 재시작 반복**: 기존 0660 root:root 노드에
  Android의 여러 UID가 접근하지 못했다. Lima 템플릿의 준비 스크립트는 전용 VM 내부의 세 노드를
  0666으로 만든다. 기존 VM은 게스트 `/usr/local/sbin/kakao-prepare-binder`와
  `~/.lima/kakao/lima.yaml`의 provision에도 수정이 반영됐는지 확인한다.
- **설치했는데 ADBKeyBoard APK를 먼저 설치하라는 오류**: `ime list -s`는 비활성 입력기를
  보여주지 않았다. `compose-boot.sh`는 `ime list -a -s`로 확인한 뒤 enable/set하도록 수정했다.
  이전 이미지라면 `scripts/lima-compose.sh up -d --build`로 다시 빌드한다.
- **Mac provenance tar 경고**: Linux tar가 `LIBARCHIVE.xattr.com.apple.provenance`를
  무시한다는 경고가 나왔으나 전송·빌드·기동은 성공했다. `COPYFILE_DISABLE=1`만으로는
  사라지지 않아 릴리즈 래퍼에 `tar --no-xattrs`를 추가했다.
- **Compose buildx 경고**: buildx가 없어도 기본 Docker builder로 이미지 빌드가 완료됐다.
- **DB 수신용 root**: redroid는 userdebug 이미지이며 boot가 `adb root`를 설정한다.
  Compose의 restart 정책과 종료된 1회성 boot는 다르므로 재실행 때 위 `run --rm boot`를 사용한다.

```bash
# 상태가 정상화되지 않을 때 (실행 중인 VM에서)
limactl shell --workdir / kakao sudo tail -n 40 /var/log/cloud-init-output.log
limactl shell --workdir / kakao sudo systemctl status kakao-binder.service --no-pager
limactl shell --workdir / kakao ls -l /dev/binderfs
limactl shell --workdir / kakao sudo docker exec kakao-redroid-1 \
  /system/bin/sh -c 'getprop sys.boot_completed; logcat -d -t 80 -s ProcessState'
```

ARM VM은 x86 VM의 /data를 가져오지 않고 새 데이터로 로그인한다.
아키텍처/키스토어/기기 바인딩 차이 때문에 데이터 이전을 보장할 수 없다.
카카오 태블릿 슬롯이 하나라고 가정하므로 **Mac 로그인 시 기존 VM 세션 해제 가능성**을
사용자가 판단해야 한다. Mac 실제 부팅은 아래와 같이 확인했고 계정 로그인은 사용자 진행 단계다.

### Mac 독립 기동 확인 (2026-10-21)

- Lima 2.2.1 / VZ / Ubuntu 24.04 arm64, CPU 2개·메모리 4GiB·디스크 20GiB.
- `kakao-redroid-1` healthy, `kakao-boot-1` Exited 0, `kakao-webui-1` 실행 확인.
- 웹 `http://127.0.0.1:8800`의 상태 ready, scrcpy 가상 화면 연결 확인.
- 카카오톡 26.8.3 ARM64 split APK는 APKPure에서 내려받아 `apksigner verify`로
  네 APK 모두 검증했다. 인증서 SHA-256은
  `2b06cc3d47782d7c497c07f17cb5f859cd6bbcb66829f3e67b96b7a44820d2ce`.
  ADBKeyBoard는 원저장소 `senzhk/ADBKeyBoard`의 APK를 설치했다.
- `doctor` ok, 로그인 화면의 `Use with Main Device` 선택 및 `QR Code Log In` 표시 확인.
  사용자가 웹 화면을 확인한 뒤 VM을 정상 종료하고 `Stopped`와 8800 리스너 해제를 확인했다.
  계정 로그인·DB 수신·메시지 송수신·VM 재기동 후 로그인 유지는 아직 검증하지 않았다.
- 첫 기동에서 host binder 노드의 0660 권한으로 Android UID의 접근이 거부돼,
  전용 Lima VM의 준비 스크립트를 0666으로 수정했다.
- 새 입력기는 비활성 상태이므로 boot가 `ime list -a -s`로 설치 여부를 확인한 뒤 활성화한다.
  배포 테스트 `tests/test_deploy.py` 4개 통과.

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

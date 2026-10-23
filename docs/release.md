# 공개 릴리즈: Apple Silicon 설치 패키지

공개 GitHub 릴리즈는 설치기·소스·배포 설정·가이드·해시 목록을 제공한다.
APK와 이미지는 설치 시 원격 배포처에서 내려받는다. 오프라인 설치 패키지가 아니다.
Lima VM 디스크, Android `/data`, ADB 키, 로그인 상태와 개인 캐시는 포함하지 않는다.

## 사용자 설치

Releases의 **`kakao-cli-0.1.3-macos-arm64.tar.gz`와 `SHA256SUMS.txt`**를 같은 폴더에 받는다.
GitHub가 자동 생성하는 Source code 압축파일 대신 첨부된 설치 패키지를 사용한다.

```bash
awk '$2 == "kakao-cli-0.1.3-macos-arm64.tar.gz"' SHA256SUMS.txt | shasum -a 256 -c -
tar -xzf kakao-cli-0.1.3-macos-arm64.tar.gz
cd kakao-cli-0.1.3-macos-arm64
brew install lima
bash scripts/setup-macos.sh
```

인터넷과 Apple Silicon Mac이 필요하다. Homebrew는 미리 설치되어 있어야 한다.
설치기는 CPU 2개·메모리 4GiB·디스크 20GiB의 `kakao` VM을 생성/시작한다.
웹 콘솔은 `http://127.0.0.1:8800`, QR 로그인은 사용자가 직접 진행한다.
기존 앱이 있으면 재설치하지 않으며 버전 업데이트도 하지 않는다.
기존 VM의 자원·provision은 템플릿 수정만으로 변경되지 않는다.

```bash
# 종료: 데이터는 보존한다.
limactl stop kakao
# 다시 시작할 때
limactl start kakao
bash scripts/setup-macos.sh
```

신규 설치기 자동 흐름은 VM/설치를 대체한 모의 테스트로 검증했다.
실제 확인한 범위는 수동 설치 후 Android 정상 부팅·doctor·웹 콘솔·로그인 화면과 정상 종료,
기존 로그인 계정의 콘솔 재생·DB 읽기·대화 목록 전환이다.
신규 자동 설치 전체 흐름·실제 메시지/파일 송신·재부팅 후 로그인 유지는 미검증이므로 preview로 배포한다.
상세 단계와 문제 해결은 [Mac 가이드](deploy-compose.md#apple-silicon-mac)를 참고한다.

v0.1.1에는 LAN HTTP에서 자동 선택되는 MSE 영상이 약 1분 후 멈추던 문제의 수정이 포함된다.
기존 원격 서버는 [수정·적용 기록](verification/2026-10-21-mse-buffer.md)의 webui 갱신 절차를
따르고, 갱신 후 브라우저도 새로고침한다.

v0.1.2는 실시간 드래그·세로 휠, 입력 순서/영상 대기열 개선, 960~1920 품질 선택,
파일 탐색 팝업과 하단 조작 버튼, CLI·TUI의 준비 상태 확인/DB 검사 재사용/변경 시 다시 그리기를 포함한다.
[웹 검증](verification/2026-10-23-console-performance.md)과
[CLI·TUI 검증](verification/2026-10-23-cli-tui-performance.md)에 측정 조건과 한계가 있다.
기존 서버는 webui만 갱신해 적용할 수 있다. **Android 60Hz 설정은 Android 컨테이너 재생성이 필요**하다.
기존 `/data`와 ADB 상태 볼륨을 유지하며 [60Hz 적용 절차](deploy-compose.md#콘솔-반응성과-2460fps-설정)를 따른다.

v0.1.3은 공통 `android-console 0.1.0` 패키지 분리와 신규 VM 설치 수정 사항을 포함한다.
기존 Kakao CLI·TUI·웹 콘솔도 공통 패키지를 사용한다. 독립 콘솔은 기본 미러 모드,
Home·최근 앱 버튼, 포트 8801을 사용하며 KakaoTalk를 자동 실행하지 않는다.
미설치 입력기 확인·설치 후 입력기 등록 대기·기본 입력기 확인을 보강하고,
독립 Lima 템플릿은 2.2 이상과 정적 포트 전달을 사용한다.
새 독립 Android VM의 최초 설치·한글 입력·파일 전송·지속 재생·재부팅 검증은
[독립 VM 검증 기록](verification/2026-10-23-android-console-fresh-vm.md)에 있다.
이 검증은 위의 KakaoTalk 신규 설치·로그인 유지 검증 범위를 대체하지 않는다.

## 독립 Android 콘솔 설치

`android-console-0.1.0-macos-arm64.tar.gz`와 `SHA256SUMS.txt`를 내려받는다.
전체 해시 파일에서 이 파일만 확인하려면 아래처럼 파일 이름을 선택한다.

```sh
awk '$2 == "android-console-0.1.0-macos-arm64.tar.gz"' SHA256SUMS.txt | shasum -a 256 -c -
tar -xzf android-console-0.1.0-macos-arm64.tar.gz
cd android-console-0.1.0-macos-arm64
brew install lima
./setup-macos.sh
# http://127.0.0.1:8801/
```

Lima 2.2 이상이 필요하다. CPU 2개·메모리 4GiB·디스크 20GiB의 별도 `android-console` VM을
사용한다. 종료는 `limactl stop android-console`, 재시작은 `limactl start --tty=false android-console`이다.
이 번들은 공통 Python 소스·Lima/Compose·설치기만 포함하며 KakaoTalk APK/코드에 의존하지 않는다.

## Python wheel 설치

릴리즈에서 두 wheel을 내려받은 뒤 함께 지정한다. `android-console`만 쓰면 첫 wheel만 필요하다.

```sh
python3 -m pip install ./android_console-0.1.0-py3-none-any.whl ./kakao_cli-0.1.3-py3-none-any.whl
android-console serve --serial DEVICE_SERIAL
```

외부 `adb`와 Python 3.11 이상이 필요하다. 공통 wheel은 Python 런타임 의존성이 없다.
Kakao wheel은 공통 wheel과 기존 cryptography 의존성을 사용한다.

## 내려받는 배포물

| 배포물 | 고정 기준 | 출처 |
|---|---|---|
| Ubuntu 24.04 arm64 | 2026-09-26 이미지 URL + SHA-256 | Ubuntu cloud-images, `deploy/lima.yaml` |
| redroid 14 64only | Docker image digest | redroid/redroid, `deploy/compose.lima.yaml` |
| Python 3.11 slim | Docker image digest | Docker Official Image, `Dockerfile` |
| 카카오톡 | 26.8.3 / 29260830 / XAPK·split 해시·서명 인증서 | APKPure, `deploy/apks.json` |
| ADBKeyBoard | 원저장소 커밋 + APK 해시·서명 인증서 | senzhk/ADBKeyBoard, `deploy/apks.json` |

APKPure는 카카오 공식 배포처가 아니다. 다운로드된 원본 APK를 수정하지 않고 설치하며,
내용·서명·인증서가 확인한 값과 다르면 설치 전에 실패한다. 배포처가 고정 파일을 삭제하거나
변경하면 설치가 중단되며, 다른 버전으로 자동 대체하지 않는다.
Ubuntu apt·Python 패키지 저장소에서 설치하는 의존성까지 완전히 고정한 재현 빌드는 아니다.
scrcpy-server는 웹 콘솔의 기존 고정 버전·해시 검증 경로로 별도 다운로드한다.

ADBKeyBoard는 [GPL-2.0](https://github.com/senzhk/ADBKeyBoard/blob/d7b27288bc8c1c8348a79ea6896bd7352c30a9fc/LICENSE)이며
대응 소스는 manifest의 고정 커밋 URL에서 확인할 수 있다. 공개 릴리즈에는 해당 APK를 넣지 않는다.
이미지와 앱에는 각각의 라이선스·이용 조건이 적용된다. 프로젝트의 기존 참고 고지는 `NOTICE`에 있다.

## 패키지 작성과 검증

저장소의 추적된 소스 파일만 묶는다. `.git`, 미추적 파일, 사용자 디스크·키·APK·컨테이너 이미지는
수집하지 않는다. 새 배포 스크립트는 Git에 추가한 뒤 빌드해야 한다.

```bash
uv run --python 3.11 --extra dev pytest -q
limactl validate deploy/lima.yaml
uv run python scripts/build-release.py dist
```

`dist/`에는 Kakao 설치 tarball과 그 파일의 `SHA256SUMS.txt`가 생성된다.
독립 runtime tarball 및 두 패키지의 wheel·sdist를 추가한 뒤 최종 해시 목록은 모든 첨부 파일에 대해 다시 생성한다.
tarball 안의 `release-files.txt`로 Git이 없는 폴더에서도 Lima로 전달할 파일을 선택한다.
소스 변경 후에는 패키지를 다시 만들어 해시를 갱신한다. 릴리즈 업로드는 빌드 스크립트가 수행하지 않는다.

`android-console` 분리 이후에는 `packages/android-console`도 tarball·Lima 전송·Docker 빌드에
포함된다. Docker는 이 로컬 소스를 명시적으로 설치한다. Python wheel을 배포할 때는
`uv build --package android-console`과 `uv build --package kakao-cli`로 두 패키지를 함께
작성해야 한다. 공통 패키지를 PyPI에 게시하기 전에는 Kakao wheel 하나만 배포하지 않는다.
독립 런타임 내보내기와 설치 방법은 [android-console 가이드](android-console.md)를 참고한다.

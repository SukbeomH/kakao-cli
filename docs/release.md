# 공개 릴리즈: Apple Silicon 설치 패키지

공개 GitHub 릴리즈는 설치기·소스·배포 설정·가이드·해시 목록을 제공한다.
APK와 이미지는 설치 시 원격 배포처에서 내려받는다. 오프라인 설치 패키지가 아니다.
Lima VM 디스크, Android `/data`, ADB 키, 로그인 상태와 개인 캐시는 포함하지 않는다.

## 사용자 설치

Releases의 **`kakao-cli-0.1.1-macos-arm64.tar.gz`와 `SHA256SUMS.txt`**를 같은 폴더에 받는다.
GitHub가 자동 생성하는 Source code 압축파일 대신 첨부된 설치 패키지를 사용한다.

```bash
shasum -a 256 -c SHA256SUMS.txt
tar -xzf kakao-cli-0.1.1-macos-arm64.tar.gz
cd kakao-cli-0.1.1-macos-arm64
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
실제 확인한 범위는 수동 설치 후 Android 정상 부팅·doctor·웹 콘솔·로그인 화면과 정상 종료다.
계정 로그인·메시지 송수신·재부팅 후 로그인 유지는 미검증이므로 preview로 배포한다.
상세 단계와 문제 해결은 [Mac 가이드](deploy-compose.md#apple-silicon-mac)를 참고한다.

v0.1.1에는 LAN HTTP에서 자동 선택되는 MSE 영상이 약 1분 후 멈추던 문제의 수정이 포함된다.
기존 원격 서버는 [수정·적용 기록](verification/2026-10-21-mse-buffer.md)의 webui 갱신 절차를
따르고, 갱신 후 브라우저도 새로고침한다.

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

`dist/`에는 설치 tarball과 `SHA256SUMS.txt`가 생성된다.
tarball 안의 `release-files.txt`로 Git이 없는 폴더에서도 Lima로 전달할 파일을 선택한다.
소스 변경 후에는 패키지를 다시 만들어 해시를 갱신한다. 릴리즈 업로드는 빌드 스크립트가 수행하지 않는다.

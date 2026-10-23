# android-console 신규 Lima VM 설치·실행 검증

2026-10-23, Apple Silicon Mac / Lima 2.2.1에서 검증했다.
기존 `kakao` VM을 재사용하지 않고 `android-console` VM을 새로 생성했다.

## 설치 경로와 구성

- `android-console` wheel을 별도 Python 가상환경에 의존성 없이 설치했다.
- 설치한 wheel의 `runtime export`로 `/Users/sukbeom/Documents/android-console-runtime`을 생성했다.
- 내보낸 `setup-macos.sh`로 Ubuntu·Docker·binderfs·redroid·입력기·웹 콘솔을 설치했다.
- VM: VZ / aarch64, CPU 2개, 메모리 4GiB, 디스크 20GiB, 게스트 페이지 크기 4096.
- Android 14 / arm64-v8a. Android 데이터는 신규 VM의 `/opt/android-console/data`에 유지한다.
- 웹 주소는 `http://127.0.0.1:8801/`. 홈 마운트·SSH agent 전달은 사용하지 않는다.
- Ubuntu 이미지는 고정 digest에 맞는 호스트 다운로드 캐시를 재사용했다.
  신규 VM의 redroid·Python 이미지는 별도로 내려받았다. 기존 Kakao Android 데이터는 복사하지 않았다.
- ADBKeyBoard는 설치 시 원본 APK를 내려받아 고정 SHA-256·서명 인증서를 확인했다.
  새 이미지에 `kakao_cli`·`cryptography`가 없고 Android에 KakaoTalk가 없음을 확인했다.

## 실제 기능 검증

| 항목 | 결과 |
| --- | --- |
| 브라우저 영상 | Chrome 475×988, H.264 WebCodecs 미러 화면 720×1280 표시 |
| 화면 제어 | Home으로 Launcher 이동, 최근 앱 화면의 overview 노드 확인, Back·Enter 전달 |
| 한글 입력 | 브라우저에서 `안드로이드 검증 123` 입력 후 Android 검색 입력 필드에서 동일 문자열 확인 |
| 기본 입력기 | `com.android.adbkeyboard/.AdbIME` 선택 확인 |
| 파일 전송 | 7552바이트 UTF-8 파일을 브라우저→VM→Android→VM으로 왕복, 각 파일의 바이트 일치 |
| 파일 SHA-256 | `5b3e16394b467bf17917e81a8837aaba7f5485d2ccb9d9eaf0770685cfdf5068` |
| 지속 재생 | 75초간 반복 세로 휠 입력, 10초마다 그려진 프레임 수 증가, JavaScript 오류 없음 |
| PNG 폴백 | 재부팅 후 PNG 렌더러에서 720×1280 실제 이미지 표시 |
| 설치기 재실행 | ADBKeyBoard의 firstInstallTime·lastUpdateTime 유지, APK 재설치 없음 |
| VM 재시작 | `limactl stop` 후 `limactl start`, binder·Docker·Android·웹 콘솔 자동 기동 |
| 영속성 | VM 파일 두 개와 Android 파일 한 개의 바이트·해시 및 기본 입력기 선택 유지 |
| 기존 Kakao | 기존 두 컨테이너의 시작 시각과 재시작 횟수(0) 유지, 8800 응답 정상 |

영속성을 확인한 검증용 파일 세 개는 검증 후 정리했다.

지속 재생은 동작·정지 여부를 확인하는 시나리오이며 최대 프레임률 성능 측정은 아니다.
수정 전후의 브라우저 관측치는 함께 저장한 [검증 데이터](2026-10-23-android-console-fresh-vm.json)를 참고한다.

## 최초 설치·재부팅에서 발견하고 수정한 문제

1. 미설치 패키지에 `pm path`를 호출하면 Android가 종료 코드 1을 반환해 설치기가 중단됐다.
   정상적으로 빈 목록을 반환하는 `pm list packages`로 존재 여부를 확인하도록 바꿨다.
2. APK 설치 완료 직후에도 InputMethodManager 등록이 늦어 `ime enable`이 실패했다.
   최대 30초간 입력기 등록을 기다린 뒤 활성화·선택하고 최종 기본 입력기를 확인한다.
   신규 VM에서 테스트용 입력기를 제거하고 수정된 설치기로 다시 설치해 성공했다.
3. 첫 VM 재시작 후 내부 웹 서버는 정상이나 동적 포트 전달이 복구되지 않았다.
   8801을 `static: true`로 설정하고 VM을 다시 시작해 맥의 loopback 접속 복구를 확인했다.
   템플릿의 최소 Lima 버전은 검증한 정적 전달 구성을 위해 2.2.0으로 지정했다.
   정적 전달 설정은 [Lima 공식 포트 전달 문서](https://lima-vm.io/docs/config/port/)를 참고했다.

설치 시 불필요한 GUI 권장 패키지를 받지 않도록 `--no-install-recommends`도 지정했다.
기존에 설치한 권장 패키지를 제거하지는 않았으므로 최소 설치 용량 측정 결과는 제공하지 않는다.

## 자동 검사

- 전체 pytest: **221 passed**, 74.52초.
- 정적 포트 설정을 포함한 최종 공통 모듈 검사: **18 passed**, 1.59초.
- 신규 회귀 검사: 최초 설치·기존 앱 유지·해시 불일치·서명 불일치·ADB 실패·입력기 등록 지연/시간 초과.
- 최종 wheel·sdist 빌드 및 독립 Docker 이미지 빌드 통과.
- 최종 실행 이미지에도 수정된 런타임 리소스가 포함되고 Kakao Python 의존성이 없음을 확인했다.

## 현재 실행과 재시작

검증 후 실행 상태를 유지했다가 사용자의 후속 요청으로 `android-console`과 `kakao` VM을 모두 정상 종료했다.
다시 시작하면 독립 콘솔은 포트 8801, 기존 Kakao 콘솔은 포트 8800을 사용한다.

```sh
# 독립 Android 콘솔: http://127.0.0.1:8801/
limactl stop android-console
limactl start --tty=false android-console

# 실행 구성·로그 확인
cd /Users/sukbeom/Documents/android-console-runtime
./lima-compose.sh ps
./lima-compose.sh logs --tail 100 webui
```

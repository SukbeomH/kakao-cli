# 2026-10-23 로컬 콘솔 반응성·프레임 검증

## 환경과 범위

- Apple Silicon Mac → Lima VZ Ubuntu arm64 → redroid Android 14 64only.
- CPU 2개, 메모리 4GiB, 디스크 20GiB 유지. Android 기본 화면 1600×2560/340dpi 유지.
- redroid guest GPU: ANGLE/SwiftShader, H.264 `media.swcodec` 소프트웨어 인코딩.
- Chrome headless, localhost WebCodecs 및 MSE 각각 확인. 원격 `192.168.0.30`에는 배포하지 않았다.
- Android 설정 앱의 목록을 위아래로 반복 스크롤했다. 계정 입력이나 메시지 송신은 테스트하지 않았다.
  검증 후 카카오톡 화면으로 복귀했다.

## 원인과 적용 내용

`dumpsys display`에서 기본 디스플레이가 15Hz, scrcpy 가상 디스플레이는 60Hz였다.
인코더 상한은 이미 60fps였지만 실제 설정 화면 스크롤은 약 15fps였다.
Compose에 `androidboot.redroid_fps=60`을 명시해 같은 /data를 사용하는 Android 컨테이너를 재생성한 후
기본 화면도 60.000004Hz로 바뀌었고, 같은 해상도의 실제 전달 프레임이 증가했다.
가상 디스플레이의 60Hz 표기만으로 실제 60fps를 판단하면 안 된다.

입력과 지연 처리:

- 영상의 pointerdown/move/up을 즉시 scrcpy로 전달. 이전의 놓은 뒤 전체 스와이프 재생을 제거했다.
- 이동은 16ms 단위로 묶고 HTTP 요청은 하나만 진행한다. 대기 중 이동은 최신 위치로 합친다.
- pointercancel/캡처 상실/탭 숨김/창 포커스 상실 시 취소하고, 연결 소실 시 서버의 2초 lease로 해제한다.
  정지한 채 누르고 있을 때는 500ms 간격으로 유지한다. scrcpy의 CANCEL 후 UP으로 포인터 상태도 정리한다.
- 휠의 pixel/line/page 단위를 정규화해 마우스 위치에서 세로 ACTION_SCROLL만 보낸다. 좌우 휠·Shift+휠은 차단한다.
  scrcpy v5 프로토콜의 축 범위 [-16,16] 및 signed fixed point 형식에 맞췄다.
- 한글 조합 완료 후 최대 60ms 단위로 텍스트를 전송. 계속 타이핑해도 전송이 미뤄지지 않으며
  Worker 완료 응답과 브라우저 직렬 큐로 텍스트 → Enter 순서를 보장한다.
- 가상 화면 긴 변 기본 1280px, 품질 960/1280/1440/1920. dpi도 비례 조정한다.
- 서버 프레임 큐 32개 + 250ms 나이 예산. 밀리면 session/config를 다시 보내고 키프레임부터 복구한다.
  이 예산은 서버 큐의 값이며 종단 간 지연 보장은 아니다. TCP 송신 버퍼와 쓰기 시간도 제한했다.
- 키프레임 간격을 1초로 요청하고, WebCodecs 디코드 대기 초과 시 리셋/키프레임 요청으로 복구한다.
- 스트림 종료 시 decoder, MSE listener, Object URL을 정리한다.
  MSE 상태바 fps는 requestVideoFrameCallback을 지원하는 브라우저에서 실제 표시 콜백을 센다.

## 측정 결과

프레임 전송률은 Annex-B 스트림의 미디어 패킷을 세었다. 반복 동작은 같은 설정 목록에서
약 x=48.5%, y=72.3%↔26.0% 구간을 각 500ms 동안 드래그한 것이다.
셸 명령 실행 간의 짧은 공백도 평균에 포함된다. 아래 수치는 측정 사례이며 성능 보장은 아니다.

| 설정 | 영상 크기 | 측정 | 전송 fps |
|---|---|---|---|
| 기존 Android 15Hz | 928×1728 | 100프레임 / 6.63초 | 15.09 |
| Android 60Hz, 기존 해상도 | 928×1728 | 347프레임 / 7.50초 | 46.24 |
| Android 60Hz, 균형 1280 | 688×1280 | 372프레임 / 6.68초 | 55.73 |
| Android 60Hz, 균형 1280, 추가 연속 구간 | 712×1280 | 583프레임 / 10초 | 58.30 |

688×1280의 1초 구간은 56, 59, 59, 59, 60, 49fps였다.
추가 10초 구간은 54, 58, 60, 58, 59, 59, 60, 60, 58, 57fps였다.
24fps 목표는 이 부하의 측정 구간에서 충족했고, 60fps 구간에도 도달했다. 60fps 고정 유지는 보장하지 않는다.
정지 화면에서는 scrcpy가 변화/반복 프레임을 적게 보내므로 이 기준을 적용하지 않는다.

부하 중 관측한 Docker CPU는 redroid 약 164%(2 CPU 중 1.64개), webui 약 3.6%였다.
메모리는 redroid 약 1.31GiB, webui 약 20MiB였다. Android top에서도 SurfaceFlinger, 설정 앱,
media.swcodec의 사용량이 컸다. 복잡한 화면에서 60fps를 지속하려면 운영 호스트에서 별도 측정하고
CPU 또는 GPU 렌더링/인코더 지원을 검증해야 한다. GPU 렌더링 활성화가 하드웨어 인코더 제공을 보장하지 않는다.

Chrome WebCodecs의 15초 스크롤 구간은 804개의 디코드 출력(평균 53.6fps)을 기록했다.
마우스를 누른 채 움직인 약 0.5초 동안 28프레임이 진행했고, 휠 전후 설정 화면 XML도 달라졌다.
입력 응답은 모두 HTTP 200, 페이지 JavaScript 오류는 없었다.

최종 MSE 검증에서는 약 125초 동안 실제 표시 콜백 6071회(평균 48.57fps)를 기록했다.
5초 단위 구간 중 최저는 162회/5초 = 32.4fps였다. 이 검증 중 VM에서 이미지 빌드도 수행했으므로
독점 부하 측정은 아니다. 이 결과로 모든 1초 구간이나 실제 운영 환경의 최저 fps를 보장할 수는 없다.
영상 currentTime은 약 8.5초에서 133.56초까지 진행했다. 한 샘플에서 readyState=2였다가
다음 샘플에서 4로 회복했고, 1분 이후 고정되는 현상은 없었다. 페이지 오류 및 입력 HTTP 실패는 없었다.
휠로 설정 목록이 이동했고, 마우스를 누른 채 드래그하는 동안 24프레임이 진행했다.
전송 58fps와 표시 49fps를 같은 수치로 취급하지 않는다.

## 재현과 회귀 검사

H.264 콘솔을 먼저 열고 설정/목록을 계속 스크롤하면서 다음을 실행한다.
새 시청자가 연결되면 키프레임 요청으로 인코더가 재설정될 수 있으므로 재생 연속성 검증과 별도로 실행한다.

```bash
python3 scripts/benchmark-video.py http://127.0.0.1:8800 --seconds 15
.venv/bin/pytest -q
```

브라우저 비교에서는 같은 창 크기, 같은 품질, 같은 Android 주기와 동작을 유지한다.
MSE의 getVideoPlaybackQuality().totalVideoFrames는 MediaSource 교체 시 초기화되므로,
세션을 가로지르는 단순 끝값-시작값을 평균 fps로 사용하지 않는다.

- 전체 Python/Node 회귀 검사: 177 passed.
- 실제 입력 JS의 지연 HTTP 테스트: down 즉시 전송, 이동 합치기, 취소, 휠 방향, PNG 폴백,
  한글 조합 보존, 연속 타이핑 중 전송, Enter 순서 확인.
- 서버: 좌표/화면 라우팅, 소유권·만료·리사이즈·재접속, scroll golden wire bytes,
  250ms 큐 만료 후 키프레임 복구, 품질별 논리 크기 확인.
- MSE: 희소 키프레임 및 이전 서버 재생, 큐가 찬 동안의 안전한 정리, 세션 리셋,
  실제 표시 기준 fps 집계, 종료 시 리소스 정리 확인.
- Compose 기본/Lima/live 구성 검증 및 셸 구문 검사 통과.

## 추가 적용: H.264 시작 시 캡처와 WebCodecs 출력

`/api/screen/info`를 조회하면 ScreenBuffer의 2초 capture lease가 갱신된다.
H.264 시작 시에도 이 API를 호출해 Worker가 기본 디스플레이의 전체 PNG를 캡처했고,
같은 Worker를 거치는 키 입력이 캡처 완료를 기다릴 수 있었다.
시작 시 기능 확인은 `/api/screen/info?peek=1`로 바꿨다. 기존 PNG polling/frame 요청은
계속 lease를 갱신하므로 PNG 폴백과 이전 클라이언트 동작은 유지한다.

WebCodecs는 첫 출력을 즉시 그리고, 다음 repaint 전에 이어지는 출력은 한 프레임만
보관해 최신 것으로 교체한다. repaint에서 남은 최신 프레임을 그리고 FPS를 한 번 센다.
한 repaint 주기에 drawImage가 최대 두 번 호출될 수 있다. 모든 출력을 무조건 rAF까지
지연시키는 초안은 첫 비교에서 휠 지연 중앙값이 약 13ms 증가해 이 방식으로 조정했다.
후속 교차 측정에서는 변동도 커서 초안이 항상 13ms 느리다고 일반화할 수 없다.
교체·리셋·메타데이터 갱신·종료 시 미사용 VideoFrame과 rAF를 정리하며,
이전 decoder 세션의 늦은 출력과 오류는 현재 세션에 반영하지 않는다.
WebCodecs FPS는 repaint 주기에 집계한 값이며 물리 모니터의 표시 완료 측정은 아니다.

### 추가 변경 전후 교차 비교

동일한 새 backend에 변경 전 HTML과 최종 HTML만 번갈아 제공했다.
`/api/screen/info`의 기본 동작은 이전과 같으므로, 이전 HTML은 PNG lease를 켜고
최종 HTML은 peek를 사용한다. 순서는 최종 → 이전 → 이전 → 최종이었다.
Chrome headless 475×988 / DPR 2, 영상 720×1280, 품질 1280, CPU 2개/4GiB를 유지했다.
모든 케이스의 처음과 끝 영상 크기가 같았으며 VM 이미지 빌드는 측정 종료 후에 수행했다.
설정 앱을 케이스마다 다시 열고 테스트가 끝나면 카카오톡으로 돌아왔다.

각 케이스에서 시작 시 사용하는 screen/info 요청을 다시 실행한 직후 안전한 위쪽 방향키를
16회 전송했다. 수치는 요청 시작부터 HTTP 응답 본문 완료까지이며 Android의 표시 완료 시간은 아니다.
전체 페이지를 처음 여는 시간의 측정도 아니다. 첫 키가 처리된 뒤 Worker가 캡처에 들어가는 경우가
있으므로 첫 한 번만 재면 문제를 놓칠 수 있다. 기존 코드의 2초 lease는 개별 키 지연을 2초로
제한하지 않으며, lease 동안 시작된 캡처는 완료까지 실행된다.

영상 반응은 케이스당 세로 휠 20회(±420, 각 동작 후 650ms 대기)를 보냈다.
Chrome CDP screencast를 메모리에서만 읽고, 설정 목록 중앙 영역의 픽셀 변화가 임계값을 넘은
첫 compositor 프레임의 타임스탬프를 브라우저 wheel 이벤트 발생 시각과 비교했다.
헤더 FPS·하단 조작 영역·스크롤바는 비교에서 제외했다. 물리 모니터나 원격 네트워크 측정은 아니다.

| 항목 | 변경 전 | 최종 변경 후 | 표본 |
|---|---:|---:|---:|
| 정보 조회 직후 키 HTTP 응답 중앙값 | 6.1ms | 3.5ms | 각 32회 |
| 같은 키 응답 p95 | 1,843.3ms | 4.9ms | 각 32회 |
| 같은 키 응답 최대 | 1,910.6ms | 5.6ms | 각 32회 |
| 휠 → 첫 화면 변화 중앙값 | 57.07ms | 53.71ms | 각 40회 |
| 같은 화면 변화 p95 | 93.61ms | 87.77ms | 각 40회 |
| 같은 화면 변화 최대 | 196.72ms | 147.26ms | 각 40회 |

입력 실패·페이지 오류·화면 변화 미검출은 없었다. PNG 캡처가 일으키는 초기 키 입력의 긴 대기는
이 측정에서 제거됐다. 평상시 휠 반응은 차이가 작고 분산도 있어 확실한 개선이라고 주장하지 않는다.
약 29.5초씩의 휠 구간에서 drawImage 호출은 577→574회, 호출에 든 JS 시간은 99.7→94.9ms였다.
이 작은 차이를 CPU/GPU 부하 개선으로 일반화할 수 없다. 연속 프레임이 몰리는 단위 회귀에서는
세 출력을 첫 출력과 최신 출력의 두 번 그리기로 줄였고, 각 VideoFrame을 정확히 한 번 닫았다.
이 변경으로 60fps가 보장되거나 원격 HTTP의 MSE 성능이 향상됐다고 해석하지 않는다.

원자료는 [console-latency.json](2026-10-23-console-latency.json)에 보관했다.
개발용 [benchmark-lima-console.cjs](../../scripts/benchmark-lima-console.cjs)는 실행 중인 로컬 Lima VM과
Chrome, `playwright-core`·`sharp`가 필요하다. 변경 전후 `webui.PAGE`를 HTML 파일로 저장하고 실행한다.
이전/새 HTML 모두와 호환되는 backend를 먼저 띄워야 하며, 설정 앱으로 화면을 전환하므로 별도 작업 중에는 실행하지 않는다.

```bash
node scripts/benchmark-lima-console.cjs /tmp/before.html /tmp/after.html
```

전체 회귀 178개 통과 후, 첫 출력 즉시 그리기로 조정한 최종 코드의 관련 회귀 49개를 다시 통과했다.
PNG peek/기존 polling, 프레임 병합·오류·세션 교체·리셋·종료 정리 검증을 포함한다.

## CLI·TUI 적용 가능성

이번 두 변경은 웹 서버의 PNG lease 및 브라우저 canvas 경로에만 적용된다.
CLI에는 영상 렌더러가 없고, TUI는 `Worker(dev)`를 ScreenBuffer 없이 생성하므로 직접적인 효과는 없다.
CLI·TUI 코드는 이 웹 평가 단계에서 변경하지 않았다. 아래 후보는 이후 별도로 적용했으며
[CLI·TUI 전후 실측](2026-10-23-cli-tui-performance.md)에 결과와 한계를 정리했다.

| 후보 | 대상과 근거 | 주의점 |
|---|---|---|
| 고정 대기 → 준비 상태 확인 | 공유 Driver의 화면 전환·입력·파일 선택에 0.4~2초 고정 sleep이 여러 곳 있다 | 짧은 간격의 dump를 남발하면 더 느려질 수 있다. 제한 시간과 성공 확인을 유지하며 개별 흐름을 측정해야 한다 |
| DB 호환성 검사 재사용 | Receiver 조회와 DB polling의 guard가 버전 dumpsys와 두 테이블 PRAGMA를 반복한다 | 기기·앱 변경/재연결·오류 때 무효화하고 주기적으로 다시 검사해야 한다. 호환성 검사를 제거하지 않는다 |
| TUI 갱신 합치기 | `_drain()`의 변경 여부 반환을 현재 loop가 사용하지 않고 150ms timeout마다 전체 화면을 다시 그린다 | 변경 시에만 다시 그리고 같은 방의 최신 표시 상태를 합칠 수 있다. 전송 명령이나 수신 메시지를 버리지 않는다 |

Worker의 `cmds.get(timeout=2.5)`는 명령이 들어오면 즉시 깨어난다.
2.5초를 사용자 입력의 고정 지연으로 해석해서는 안 된다. 이미 진행 중인 느린 DB/UI 작업은
입력을 막을 수 있으며, 주기적 새 메시지 발견은 조회 간격과 실제 조회 시간의 영향을 받는다.

## 근거

- [redroid 설정](https://github.com/remote-android/redroid-doc#configuration): GPU 비활성 환경의 기본 15fps와 redroid_fps.
- [scrcpy v5.0.1 scroll 직렬화](https://github.com/Genymobile/scrcpy/blob/v5.0.1/app/src/control_msg.c): 축 /16 정규화와 21-byte 패킷.
- [scrcpy v5.0.1 입력 주입](https://github.com/Genymobile/scrcpy/blob/v5.0.1/server/src/main/java/com/genymobile/scrcpy/control/Controller.java): 포인터 해제 및 스크롤.
- [scrcpy 인코더](https://github.com/Genymobile/scrcpy/blob/v5.0.1/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java): 프레임 주기와 키프레임 옵션.

## 파일 탐색 팝업과 스크롤 분리

파일 목록을 모든 화면 크기에서 상단 파일 탐색 버튼으로 여는 native dialog로 옮겼다.
화면 아래에 파일 패널을 이어 붙이는 모바일 레이아웃을 제거했고 파일 목록의 overscroll도 차단한다.
팝업이 열려도 기기 화면 영역 크기가 바뀌지 않으며, 팝업 안의 휠이 기기로 전달되지 않는다.
브라우저와 서버 모두 좌우 스크롤을 전달하지 않는다. 직접 드래그 제스처는 유지한다.

`tests/webui_files_dialog.js`는 가짜 화면/파일 응답으로 1200×800, 390×844, 640×360에서
기기 휠과 목록 스크롤 분리, 가로/Shift 휠 차단, 키보드 포커스 제한, Esc/버튼/배경 닫기 및 화면 크기 유지를 검사한다.
Android 입력이나 실제 파일 전송 없이 실행한다.

## Android 하단 내비게이션 숨김

가상 디스플레이 생성 시 `vd_system_decorations=false`를 전달한다.
[공식 scrcpy 옵션](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/virtual-display.md#system-decorations)을 사용하며,
스트리밍 픽셀을 잘라내는 방식이 아니므로 앱 하단 내용과 터치 좌표를 유지한다.
로컬 재기동 후 가상 display 13(720×1280)에서 NavigationBar 창이 없고
FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS도 빠진 것을 dumpsys로 확인했다. 스트림 연결은 정상이었다.
가상 디스플레이의 IME 정책은 local을 유지하며, 기기 화면 아래 Back 컨트롤은 그대로 제공한다.
영상 회귀 검사 21개 통과.

## 조작 버튼과 FPS 배치

FPS는 상단 헤더, Back/Enter/클립보드 버튼은 상태 표시줄 바로 위의 하단 조작 영역에 배치했다.
기기 화면 위의 기존 버튼 줄과 인라인 키보드 안내를 제거했다.
475×988, 390×844, 320×600, 1200×800, 640×360에서 헤더의 FPS 및
기기 화면 → 조작 버튼 → 상태 표시줄 순서와 화면 안쪽 배치를 브라우저로 확인했다.
파일 팝업 회귀 검사 3개 뷰포트 및 입력/라우팅 검사도 통과했다.

앱 전용 가상 화면을 벗어나는 Home 버튼은 제거했다. 키보드 Home/End의 텍스트 커서 이동은 유지한다.

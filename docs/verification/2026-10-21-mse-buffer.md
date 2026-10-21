# LAN 웹 콘솔 MSE 화면 정지 수정 (2026-10-21)

## 증상과 재현

사용 환경은 Chrome 자동 렌더러, 원격 호스트 `http://192.168.0.30:8800`이다.
조작 중에도 약 1분 뒤 화면이 멈춘다는 보고가 있었다. 이 LAN HTTP 경로는 보안 컨텍스트가
아니므로 WebCodecs 대신 MSE를 선택한다. 원격 페이지 소스에도 기존 버퍼 정리 코드가 있었다.

같은 코드의 로컬 Android/Lima 환경에서 `?video=mse`로 재현했다.
카카오톡 로그인 입력창에 포커스를 두어 커서가 깜빡이게 했고, 계정 입력이나 로그인·전송은 하지 않았다.
MSE의 `currentTime`이 `20.371960`에서 멈추고 `buffered`가 빈 배열이 되었다.
동시에 서버의 Annex-B 스트림은 60.2초 동안 660프레임을 전달했다.
비교한 WebCodecs 경로는 1분을 넘겨 프레임이 계속 증가했다.

## 원인과 수정

기존 코드는 20초 이상 쌓인 버퍼에서 `remove(start, currentTime - 10)`을 호출했다.
[MSE coded frame removal](https://www.w3.org/TR/media-source-2/#sourcebuffer-coded-frame-removal)은
영상 의존성 때문에 제거 끝점 이후의 다음 랜덤 액세스 포인트까지 영향을 줄 수 있다.
scrcpy 출력에서 키프레임 간격이 길어지면 재생 중인 GOP의 필요한 프레임까지 제거돼 화면이 멈췄다.

- fMP4 전송에 타입 4 `{key_time}`을 추가해 실제 키프레임의 미디어 타임라인 위치를 전달한다.
  원본 scrcpy PTS 대신 연속 fMP4 DTS를 사용한다.
- 재생 위치보다 10초 이상 이전인 키프레임을 기준으로, 그 키프레임을 보존하며 오래된 버퍼만 지운다.
  sequence 모드의 시간 반올림을 위해 끝점에 1ms 여유를 둔다.
- 안전한 키프레임을 아직 모르면 제거하지 않는다. 세션이 바뀌면 키프레임 목록도 초기화한다.
- append 큐가 계속 차 있어도 정리가 가능하도록 안전한 정리를 append보다 먼저 수행한다.
- 키프레임 메타데이터가 없는 구 서버와 연결된 새 클라이언트는 임의 버퍼 제거를 건너뛴다.

## 검증과 적용

- 실제 클라이언트의 `mseSink`를 Node 모의 SourceBuffer에서 실행하는 회귀 테스트 추가.
  기존 코드로 같은 테스트를 실행하면 현재 GOP를 보존하지 못해 실패하고 수정본은 통과한다.
- 초기 키프레임만 있는 경우, 이전 서버, 대기 중인 append, 새 세션의 시간 초기화를 검사한다.
- HTTP 엔드포인트 테스트에서 타입 4가 키프레임 앞에 전달되고, 긴 PTS 간격에도
  `key_time`이 fMP4 미디어 시간과 일치하는지 검사한다.
- 전체 테스트: **171 passed**.
- 수정본 MSE를 실제 브라우저에서 연속 재생했다. 재생 시간 `65.422303`초에서
  버퍼 `[54.041786, 65.710227]`, `125.266224`초에서 `[108.574992, 125.543942]`를 확인했다.
  두 차례 오래된 GOP를 제거한 뒤에도 readyState 4·paused false였고 브라우저 오류는 없었다.
  가만히 있는 화면의 정상적인 무프레임 상태와 구분하려고 입력창의 커서 깜빡임을 사용했다.
- 원격 서버에는 이 맥의 SSH 키로 접근할 수 없어 직접 배포하지 않는다. 사용자가 저장소를
  갱신한 뒤 아래와 같이 **webui만** 다시 빌드·교체한다.

```bash
# 원격 호스트의 실제 배포 저장소에서
git pull --ff-only
export COMPOSE_DISABLE_ENV_FILE=1
docker compose -f deploy/compose.yaml -f deploy/compose.live.yaml build webui
docker compose -f deploy/compose.yaml -f deploy/compose.live.yaml up -d --no-deps webui
```

이후 브라우저를 새로고침해 새 버퍼 정리 코드도 불러온다. 기존 HTML을 계속 열어 두면
서버를 교체해도 이전 클라이언트 코드가 남는다. redroid 재시작과 계정 재로그인은 필요하지 않다.
`~/kakao-compose`가 Git 저장소가 아닌 archive 배포본이면 먼저 최신 코드를 그 배포 경로에
동기화한 뒤 build/up 명령만 실행한다([배포 문서](../deploy-compose.md) 참고).

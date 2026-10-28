#!/usr/bin/env bash
# mounts=[]를 유지하며 Git 추적 코드만 게스트에 전달한다. DB/홈/키는 전송하지 않는다.
# limactl shell 방식 참고: kakaotalk-bridge(MIT), NOTICE 참조.
set -euo pipefail
TASK_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_LIMA_INSTANCE="${KAKAO_LIMA_INSTANCE:-kakao}"
TASK_LIMA_DIR=/opt/kakao-cli
command -v limactl >/dev/null || { echo 'Lima를 먼저 설치하세요' >&2; exit 1; }
[[ $# -gt 0 ]] || set -- config --quiet
for arg in "$@"; do
  case "$arg" in
    -v|--volumes|--volumes=*|--renew-anon-volumes)
      echo '데이터/키/캐시 볼륨 삭제·교체 옵션은 금지합니다' >&2; exit 2 ;;
  esac
done
# 실행/기동 여부는 사용자가 명시한 compose 인자에 따른다. 자동으로 VM을 만들지 않는다.
limactl shell --workdir / "$TASK_LIMA_INSTANCE" sudo mkdir -p "$TASK_LIMA_DIR"
# 경로 이름만 조회한다. 비밀·DB 확장자는 추적돼 있어도 제외하고 파일 내용을 읽지 않는다.
task_source_files() {
  if [[ -e "$TASK_REPO_ROOT/.git" ]]; then
    git -C "$TASK_REPO_ROOT" ls-files -z -- Dockerfile .dockerignore pyproject.toml README.md NOTICE src packages scripts deploy \
      ':(exclude)**/.env*' ':(exclude).env*' ':(exclude)**/*.key' ':(exclude)**/*.pem' \
      ':(exclude)**/*.db*' ':(exclude)**/*.sqlite*'
  elif [[ -f "$TASK_REPO_ROOT/release-files.txt" ]]; then
    while IFS= read -r path; do
      case "$path" in
        /*|../*|*/../*|*/..|./*|*/./*|*\\*|*.key|*.pem|*.db*|*.sqlite*|.env*|*/.env*)
          echo '잘못된 릴리즈 파일 목록입니다' >&2; return 1 ;;
        Dockerfile|.dockerignore|pyproject.toml|README.md|NOTICE|src/*|packages/*|scripts/*|deploy/*) ;;
        *) continue ;;
      esac
      [[ -f "$TASK_REPO_ROOT/$path" && ! -L "$TASK_REPO_ROOT/$path" ]] \
        || { echo "릴리즈 파일 누락: $path" >&2; return 1; }
      printf '%s\0' "$path"
    done < "$TASK_REPO_ROOT/release-files.txt"
  else
    echo '.git 또는 release-files.txt가 필요합니다. Releases의 설치 패키지를 사용하세요.' >&2
    return 1
  fi
}
task_source_files \
  | COPYFILE_DISABLE=1 tar --no-xattrs -C "$TASK_REPO_ROOT" --null -T - -cf - \
  | limactl shell --workdir / "$TASK_LIMA_INSTANCE" sudo tar -xf - -C "$TASK_LIMA_DIR"
# A marker created by setup-vm-vpn.sh keeps this opt-in across later updates.
TASK_NETWORK_ARGS=()
TASK_NETWORK_MODE="${KAKAO_NETWORK:-}"
if [[ -z "$TASK_NETWORK_MODE" ]]; then
  TASK_NETWORK_MODE="$(limactl shell --workdir / "$TASK_LIMA_INSTANCE" sudo cat /opt/kakao-cli/.network-enabled 2>/dev/null || true)"
fi
if [[ "$TASK_NETWORK_MODE" == 1 || "$TASK_NETWORK_MODE" == enabled ]]; then
  TASK_NETWORK_ARGS=(-f deploy/compose.network.yaml)
fi
# 절대 경로 --workdir를 써서 맥의 현재 디렉터리를 게스트에서 찾지 않게 한다.
exec limactl shell --workdir "$TASK_LIMA_DIR" "$TASK_LIMA_INSTANCE" \
  sudo env COMPOSE_DISABLE_ENV_FILE=1 \
  "KAKAO_ADB_SECURE=${KAKAO_ADB_SECURE:-0}" "KAKAO_WEB_BIND=${KAKAO_WEB_BIND:-127.0.0.1}" \
  "KAKAO_ANDROID_FPS=${KAKAO_ANDROID_FPS:-60}" \
  "KAKAO_ANDROID_DATA=${KAKAO_ANDROID_DATA:-/opt/redroid/data}" docker compose --project-name kakao \
  -f deploy/compose.yaml -f deploy/compose.lima.yaml "${TASK_NETWORK_ARGS[@]}" "$@"

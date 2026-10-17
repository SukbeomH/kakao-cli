#!/usr/bin/env bash
# End-to-end for the live device-screen view/control (stage C). Runs from the
# control host; deploys to the VM, starts `kakao webui --allow-screen`, and:
#  - device-side checks (tests/webui_screen_check.py over ssh): frame, tap→device
#    (toggle Chats/Friends tab, dump-verified), frame change, fps, idle-stop
#  - browser overlay (webui_screen.js): desktop + mobile open/frame/close
#  - security: a non-local bind WITHOUT --allow-screen returns 403
#
#   tests/e2e_screen.sh
set -u
VM="${VM:-agent@192.168.0.30}"
VM_IP="${VM_IP:-192.168.0.30}"
SER="${SER:-127.0.0.1:5555}"
PORT="${PORT:-8809}"
NOFLAG_PORT="${NOFLAG_PORT:-8810}"
REMOTE="${REMOTE:-~/kakao-cli}"
SESS="kscreen"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export NODE_PATH="${NODE_PATH:-/home/agent/kakao-tools/node_modules}"
export CHROME="${CHROME:-/home/agent/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome}"

fail(){ echo "FAIL: $*" >&2; cleanup; exit 1; }
cleanup(){ ssh "$VM" "tmux kill-session -t $SESS 2>/dev/null; tmux kill-session -t ${SESS}N 2>/dev/null; rm -f /sdcard/sc.xml" 2>/dev/null; }
trap cleanup EXIT

echo "1) deploy"
scp -q "$ROOT/src/kakao_cli/screen.py" "$ROOT/src/kakao_cli/worker.py" \
       "$ROOT/src/kakao_cli/webui.py" "$ROOT/src/kakao_cli/adb.py" \
       "$ROOT/src/kakao_cli/cli.py" "$VM:$REMOTE/src/kakao_cli/" || fail "scp failed"
scp -q "$ROOT/tests/webui_screen_check.py" "$VM:/tmp/webui_screen_check.py" || fail "scp check failed"

echo "2) start webui --allow-screen on $PORT"
ssh "$VM" "tmux kill-session -t $SESS 2>/dev/null; tmux new-session -d -s $SESS \
  'cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli webui --host 0.0.0.0 --port $PORT \
   --allow-screen --serial $SER'" || fail "start failed"
ok=0
for i in $(seq 1 40); do
  c=$(curl -s -o /dev/null -w '%{http_code}' "http://$VM_IP:$PORT/api/screen/frame")
  [ "$c" = "200" ] && { ok=1; break; }
  sleep 2
done
[ "$ok" = 1 ] || fail "no frame from $VM_IP:$PORT"

echo "3) device-side checks (frame/tap/change/fps/idle)"
ssh "$VM" "cd $REMOTE && BASE=http://127.0.0.1:$PORT SER=$SER python3 /tmp/webui_screen_check.py" \
  || fail "device-side screen checks failed"

echo "4) browser overlay (desktop)"
node "$ROOT/tests/webui_screen.js" "http://$VM_IP:$PORT" || fail "desktop overlay failed"
echo "5) browser overlay (mobile)"
MOBILE=1 node "$ROOT/tests/webui_screen.js" "http://$VM_IP:$PORT" || fail "mobile overlay failed"

echo "6) security: non-local bind without --allow-screen blocks the screen API"
ssh "$VM" "tmux kill-session -t ${SESS}N 2>/dev/null; tmux new-session -d -s ${SESS}N \
  'cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli webui --host 0.0.0.0 --port $NOFLAG_PORT --serial $SER'"
sleep 4
code=$(curl -s -o /dev/null -w '%{http_code}' "http://$VM_IP:$NOFLAG_PORT/api/screen/frame")
ssh "$VM" "tmux kill-session -t ${SESS}N 2>/dev/null"
[ "$code" = "403" ] || fail "screen API not blocked without --allow-screen (got $code)"
echo "   blocked (403)"

echo "PASS: screen e2e (frame + tap device-verified + overlay + security)"

#!/usr/bin/env bash
# End-to-end web UI test. Runs from the CONTROL host (which has a cached
# Chromium + playwright-core); deploys the code to the Android host (VM),
# starts `kakao webui` there, then drives the page with a headless browser
# (tests/webui_browser.js) and asserts a Korean message reaches the self-chat
# (verified independently via adb readback over ssh). A curl smoke would only
# prove the API; this proves the inlined JS.
#
#   SELF_NAME="<self-chat row name>" tests/e2e_webui.sh
#
# Env (with defaults): VM=agent@192.168.0.30  VM_IP=192.168.0.30  SER=127.0.0.1:5555
#   PORT=8808  REMOTE=~/kakao-cli
#   NODE_PATH=/home/agent/kakao-tools/node_modules
#   CHROME=/home/agent/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome
set -u
VM="${VM:-agent@192.168.0.30}"
VM_IP="${VM_IP:-192.168.0.30}"
SER="${SER:-127.0.0.1:5555}"
PORT="${PORT:-8808}"
REMOTE="${REMOTE:-~/kakao-cli}"
SESS="kwebtest"
MSG="e2e-web-$(date +%H%M%S)-한글"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export NODE_PATH="${NODE_PATH:-/home/agent/kakao-tools/node_modules}"
export CHROME="${CHROME:-/home/agent/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome}"

fail(){ echo "FAIL: $*" >&2; cleanup; exit 1; }
cleanup(){ ssh "$VM" "tmux kill-session -t $SESS 2>/dev/null; rm -f /sdcard/kcv.xml" 2>/dev/null; }
trap cleanup EXIT

[ -n "${SELF_NAME:-}" ] || fail "SELF_NAME env required (the self-chat row name)"

echo "1) deploy modules to VM"
scp -q "$ROOT/src/kakao_cli/worker.py" "$ROOT/src/kakao_cli/webui.py" \
       "$ROOT/src/kakao_cli/tui.py" "$ROOT/src/kakao_cli/cli.py" \
       "$VM:$REMOTE/src/kakao_cli/" || fail "scp failed"

echo "2) start webui on VM (0.0.0.0:$PORT)"
ssh "$VM" "tmux kill-session -t $SESS 2>/dev/null; \
  tmux new-session -d -s $SESS 'cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli webui \
    --host 0.0.0.0 --port $PORT --serial $SER'" || fail "could not start webui"

echo "3) wait for the API to answer"
ok=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://$VM_IP:$PORT/" 2>/dev/null)
  [ "$code" = "200" ] && { ok=1; break; }
  sleep 1
done
[ "$ok" = 1 ] || fail "webui did not come up on $VM_IP:$PORT"

echo "4) drive the page with a headless browser"
SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_browser.js" "http://$VM_IP:$PORT" "$MSG" \
  || fail "browser driver failed"

echo "5) assert delivered (adb readback over ssh)"
ok=0
for i in $(seq 1 8); do
  if ssh "$VM" "adb -s $SER shell uiautomator dump /sdcard/kcv.xml >/dev/null 2>&1; \
                adb -s $SER shell cat /sdcard/kcv.xml 2>/dev/null" | grep -q -- "$MSG"; then
    ok=1; break
  fi
  sleep 2
done
[ "$ok" = 1 ] || fail "message not found in self-chat"

echo "PASS: webui e2e (browser sent & device-verified)"

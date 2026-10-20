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
       "$ROOT/src/kakao_cli/driver.py" "$ROOT/src/kakao_cli/adb.py" \
       "$ROOT/src/kakao_cli/screen.py" "$ROOT/src/kakao_cli/fs.py" \
       "$VM:$REMOTE/src/kakao_cli/" || fail "scp failed"

echo "1b) bump the self-chat to the top of the chat list (memo to self)"
# the room list only shows the visible rows; real conversations can push the
# self-chat off-screen, so send one safe memo to bring it back to the top
ssh "$VM" "cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli memo 'e2e-setup-$(date +%H%M%S)' \
  --serial $SER --json" | grep -q '"ok": true' || fail "self-chat bump (memo) failed"

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

readback(){ # readback <ere> — true if the regex appears in the open chat dump
  local pat=$1 i
  for i in $(seq 1 8); do
    if ssh "$VM" "adb -s $SER shell uiautomator dump /sdcard/kcv.xml >/dev/null 2>&1; \
                  adb -s $SER shell cat /sdcard/kcv.xml 2>/dev/null" | grep -qE -- "$pat"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

echo "4) text send via the browser (desktop)"
SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_browser.js" "http://$VM_IP:$PORT" "$MSG" \
  || fail "browser text driver failed"
readback "$MSG" || fail "text message not found in self-chat"
echo "   text OK (device-verified)"

echo "4m) text send via the browser (mobile emulation: tap row → convo → 보내기 → ← 목록)"
MMSG="e2e-mob-$(date +%H%M%S)-한글"
MOBILE=1 SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_browser.js" "http://$VM_IP:$PORT" "$MMSG" \
  || fail "mobile browser driver failed"
readback "$MMSG" || fail "mobile message not found in self-chat"
echo "   mobile OK (device-verified)"

echo "4s) scroll position is kept when a message arrives while scrolled up"
SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_scroll.js" "http://$VM_IP:$PORT" \
  || fail "scroll-persistence test failed"
echo "   scroll OK"

echo "4f) stage-A features (2-line rows, quick switcher, textarea newline)"
FMSG="e2e-feat-$(date +%H%M%S)-한글"
SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_features.js" "http://$VM_IP:$PORT" "$FMSG" \
  || fail "feature test failed"
readback "$FMSG" || fail "feature message not found in self-chat"
echo "   features OK (device-verified)"

echo "4i) IME: Enter while composing must not double-send"
SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_ime.js" "http://$VM_IP:$PORT" \
  || fail "IME guard test failed"
echo "   IME OK"

echo "5) document (zip) send via the '+' picker"
ZIP="/tmp/kc_e2e_${SESS}_$(date +%H%M%S).zip"
python3 -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('hello.txt','hi'); z.close()" "$ZIP" \
  || fail "could not build test zip"
FILE="$ZIP" SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_browser.js" "http://$VM_IP:$PORT" \
  || { rm -f "$ZIP"; fail "browser zip driver failed"; }
# confirm a .zip File bubble landed (name may be elided in the UI, so match the
# bubble's extension + the "Open File" affordance rather than the full name)
readback '\.zip.*Open File' || { rm -f "$ZIP"; fail "zip not found in self-chat"; }
rm -f "$ZIP"
echo "   document OK (device-verified)"

echo "6) image (png) send via the share picker — exercises the ACTION_SEND branch"
PNG="/tmp/kc_e2e_${SESS}_$(date +%H%M%S).png"
python3 -c "import base64,sys; open(sys.argv[1],'wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHElEQVR4nGNgYGD4z4AKGEeFRoVGhUaFhq0QAFYAEQF9wh6pAAAAAElFTkSuQmCC'))" "$PNG" \
  || fail "could not build test png"
FILE="$PNG" SELF_NAME="$SELF_NAME" node "$ROOT/tests/webui_browser.js" "http://$VM_IP:$PORT" \
  || { rm -f "$PNG"; fail "browser image driver failed"; }
rm -f "$PNG"
# image bubbles carry no filename; the worker re-anchors after the share intent,
# so poll the live state for a [Photo] (it appears a beat after status "sent")
img_ok=0
for i in $(seq 1 12); do
  if curl -s "http://$VM_IP:$PORT/api/state" | grep -q '\[Photo\]'; then img_ok=1; break; fi
  sleep 2
done
[ "$img_ok" = 1 ] || fail "image not confirmed ([Photo] not in state)"
echo "   image OK ([Photo] in state)"

echo "7) receive the document (click ⤓ → browser download)"
RZIP="/tmp/kc_recv_${SESS}.zip"; rm -f "$RZIP"
MATCH=".zip" OUT="$RZIP" SELF_NAME="$SELF_NAME" \
  node "$ROOT/tests/webui_recv.js" "http://$VM_IP:$PORT" || fail "receive zip failed"
python3 -c "import sys,zipfile; sys.exit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)" "$RZIP" \
  || { rm -f "$RZIP"; fail "received file is not a valid zip"; }
rm -f "$RZIP"; echo "   document received (valid zip)"

echo "8) receive the image (click ⤓ → browser download)"
RPNG="/tmp/kc_recv_${SESS}.png"; rm -f "$RPNG"
MATCH="[Photo]" OUT="$RPNG" SELF_NAME="$SELF_NAME" \
  node "$ROOT/tests/webui_recv.js" "http://$VM_IP:$PORT" || fail "receive image failed"
python3 -c "import sys; d=open(sys.argv[1],'rb').read(8); sys.exit(0 if d.startswith(b'\x89PNG\r\n\x1a\n') else 1)" "$RPNG" \
  || { rm -f "$RPNG"; fail "received image is not a valid PNG"; }
rm -f "$RPNG"; echo "   image received (valid PNG)"

echo "PASS: webui e2e (send+receive: text, document, image — all device-verified)"

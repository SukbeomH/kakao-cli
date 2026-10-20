#!/usr/bin/env bash
# End-to-end for the web UI console (device screen + file managers). Runs from the
# control host; deploys to the VM and starts `kakao webui --allow-screen --fs-root`.
# Verifies with the device/disk directly: push lands in /sdcard/Download, the pull
# comes back as <stem>-1 (no overwrite), and keyboard capture types Korean into
# KakaoTalk's message box. Nothing is sent; the typed draft and the test files are
# cleaned up afterwards.
#
#   tests/e2e_console.sh
set -u
VM="${VM:-agent@192.168.0.30}"
VM_IP="${VM_IP:-192.168.0.30}"
SER="${SER:-127.0.0.1:5555}"
PORT="${PORT:-8811}"
REMOTE="${REMOTE:-~/kakao-cli}"
ROOTDIR="${ROOTDIR:-/home/agent/kc-console-e2e}"
SESS="kconsole"
TS="$(date +%H%M%S)"
PUSH="kc-e2e-$TS.txt"
KBD="kbd테스트$TS"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export NODE_PATH="${NODE_PATH:-/home/agent/kakao-tools/node_modules}"
export CHROME="${CHROME:-/home/agent/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome}"

cleanup(){ ssh "$VM" "adb -s $SER shell am broadcast -a ADB_CLEAR_TEXT >/dev/null 2>&1; \
  adb -s $SER shell rm -f '/sdcard/Download/$PUSH' >/dev/null 2>&1; \
  tmux kill-session -t $SESS 2>/dev/null; rm -rf '$ROOTDIR'" 2>/dev/null; }
fail(){ echo "FAIL: $*" >&2; cleanup; exit 1; }
trap cleanup EXIT
# (opens the self-chat via "me"; no name needed)

dump(){ ssh "$VM" "adb -s $SER shell uiautomator dump /sdcard/kcc.xml >/dev/null 2>&1; \
                   adb -s $SER shell cat /sdcard/kcc.xml 2>/dev/null"; }

echo "1) deploy + local root with a test file"
scp -q "$ROOT"/src/kakao_cli/{fs,webui,worker,cli,driver,adb,screen}.py "$VM:$REMOTE/src/kakao_cli/" \
  || fail "scp failed"
ssh "$VM" "rm -rf '$ROOTDIR'; mkdir -p '$ROOTDIR'; echo 'console e2e $TS' > '$ROOTDIR/$PUSH'"

echo "2) start webui --allow-screen --fs-root $ROOTDIR"
ssh "$VM" "tmux kill-session -t $SESS 2>/dev/null; tmux new-session -d -s $SESS \
  'cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli webui --host 0.0.0.0 --port $PORT \
   --allow-screen --fs-root $ROOTDIR --serial $SER'"
ok=0
for i in $(seq 1 40); do
  n=$(curl -s "http://$VM_IP:$PORT/api/state" | python3 -c "import sys,json;print(len(json.load(sys.stdin)['rooms']))" 2>/dev/null)
  [ "${n:-0}" -gt 0 ] 2>/dev/null && { ok=1; break; }
  sleep 1.5
done
[ "$ok" = 1 ] || fail "webui did not come up"

echo "3) open the self-chat and locate the message box on the device"
# "me" → Friends tab → My Chatroom: works no matter where the self-chat sits in
# the chat list (open-by-name only sees the visible rows)
curl -s -X POST "http://$VM_IP:$PORT/api/open" -H 'Content-Type: application/json' \
  -d '{"room":"me"}' >/dev/null
# SAFETY: only type into a chat whose visible messages are ALL mine (= self-chat);
# never into a real conversation
self_ok=0
for i in $(seq 1 30); do
  v=$(curl -s "http://$VM_IP:$PORT/api/state" | python3 -c "
import sys,json
d=json.load(sys.stdin); m=[x for x in d['messages'] if x.get('kind')!='date']
print('ok' if d.get('room')=='me' and m and all(x.get('is_mine') for x in m) else 'no')" 2>/dev/null)
  [ "$v" = ok ] && { self_ok=1; break; }
  sleep 2
done
[ "$self_ok" = 1 ] || fail "self-chat not confirmed (refusing to type into another chat)"
echo "   self-chat confirmed (all visible messages are mine)"
XY=""
for i in $(seq 1 15); do
  XY=$(dump | python3 -c "
import re,sys
m=re.search(r'resource-id=\"com.kakao.talk:id/message_edit_text\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', sys.stdin.read())
print('%d %d'%((int(m[1])+int(m[3]))//2,(int(m[2])+int(m[4]))//2) if m else '')")
  [ -n "$XY" ] && break
  sleep 2
done
[ -n "$XY" ] || fail "message box not found on device"
read IX IY <<<"$XY"; echo "   message box at device ($IX,$IY)"

echo "4) browser: console mode, stream, android/local panes, push/pull, keyboard"
PUSH_NAME="$PUSH" INPUT_X="$IX" INPUT_Y="$IY" KBD_TEXT="$KBD" \
  SHOT="${SHOT:-/tmp/webui_console.png}" node "$ROOT/tests/webui_console.js" "http://$VM_IP:$PORT" \
  || fail "browser console driver failed"

echo "5) verify on device + disk"
dev_txt=$(ssh "$VM" "adb -s $SER shell cat '/sdcard/Download/$PUSH'" | tr -d '\r')
[ "$dev_txt" = "console e2e $TS" ] || fail "pushed file missing/different on device: '$dev_txt'"
echo "   push OK (/sdcard/Download/$PUSH)"
back="${PUSH%.txt}-1.txt"
loc_txt=$(ssh "$VM" "cat '$ROOTDIR/$back'")
[ "$loc_txt" = "console e2e $TS" ] || fail "pulled file missing/different on disk: '$loc_txt'"
ssh "$VM" "test -f '$ROOTDIR/$PUSH'" || fail "original local file was touched"
echo "   pull OK ($back, original untouched)"
got=0
for i in $(seq 1 8); do
  dump | grep -q "$KBD" && { got=1; break; }
  sleep 2
done
[ "$got" = 1 ] || fail "keyboard text not found in the message box"
echo "   keyboard OK ('$KBD' typed into the message box, not sent)"

echo "PASS: console e2e (stream + push/pull disk-verified + keyboard device-verified)"

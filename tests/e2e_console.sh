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

UPL="up-$TS.bin"; UPA="dev-$TS.bin"; WORK="$(mktemp -d)"
cleanup(){ rm -rf "$WORK"; ssh "$VM" "adb -s $SER shell am broadcast -a ADB_CLEAR_TEXT >/dev/null 2>&1; \
  adb -s $SER shell rm -f '/sdcard/Download/$PUSH' '/sdcard/Download/$UPA' >/dev/null 2>&1; \
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
  c=$(curl -s -o /dev/null -w '%{http_code}' "http://$VM_IP:$PORT/api/screen/info")
  [ "$c" = 200 ] && { ok=1; break; }
  sleep 1.5
done
[ "$ok" = 1 ] || fail "webui did not come up"

echo "2i) IME regression (console keyboard capture, spied — nothing reaches the device)"
node "$ROOT/tests/webui_ime.js" "http://$VM_IP:$PORT" || fail "IME regression failed"

echo "3) open the self-chat and locate the message box on the device"
# The web UI has no chat anymore, so drive it with the CLI's driver on the VM:
# "me" → Friends tab → My Chatroom works wherever the self-chat sits in the list.
# SAFETY: only type into a chat whose visible messages are ALL mine (= self-chat).
XY=$(ssh "$VM" "cd $REMOTE && PYTHONPATH=src python3 -c \"
import json
from kakao_cli.adb import connect
from kakao_cli.driver import Driver
from kakao_cli import RID
d = Driver(connect('$SER')); d.open_self_chat()
s = d.screen(); m = [x for x in d._messages_detailed(s) if x.get('kind') != 'date']
inp = s.first(rid=RID['input'])
ok = bool(m) and all(x.get('is_mine') for x in m) and inp is not None
print('%d %d' % inp.center if ok else 'NO')
\"" 2>/dev/null | tail -1)
[ -n "$XY" ] && [ "$XY" != NO ] || fail "self-chat not confirmed (refusing to type into another chat)"
echo "   self-chat confirmed (all visible messages are mine)"
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

echo "6) transfers: browser⇄VM and browser⇄device (sha256)"
head -c 6000000 /dev/urandom > "$WORK/$UPL"; head -c 1500000 /dev/urandom > "$WORK/$UPA"
mkdir -p "$WORK/out"
FILE_LOCAL="$WORK/$UPL" FILE_ANDROID="$WORK/$UPA" OUT="$WORK/out" SHOT="${SHOT2:-/tmp/webui_transfer.png}" \
  node "$ROOT/tests/webui_transfer.js" "http://$VM_IP:$PORT" || fail "browser transfer driver failed"
h(){ sha256sum "$1" | cut -d' ' -f1; }
hl=$(h "$WORK/$UPL"); ha=$(h "$WORK/$UPA")
[ "$(ssh "$VM" "sha256sum '$ROOTDIR/$UPL'" | cut -d' ' -f1)" = "$hl" ] || fail "VM copy hash mismatch"
[ "$(h "$WORK/out/dl-local.bin")" = "$hl" ] || fail "VM download hash mismatch"
[ "$(ssh "$VM" "adb -s $SER shell sha256sum '/sdcard/Download/$UPA'" | cut -d' ' -f1)" = "$ha" ] \
  || fail "device copy hash mismatch"
[ "$(h "$WORK/out/dl-android.bin")" = "$ha" ] || fail "device download hash mismatch"
echo "   sha256 OK: upload→VM, VM→download, drop→device, device→download"
ssh "$VM" "ls '$ROOTDIR'" | grep -q '\.part$' && fail ".part left behind"
echo "6m) mobile: upload + download in the stacked console"
rm -f "$WORK/out/dl-local.bin"; head -c 300000 /dev/urandom > "$WORK/m-$TS.bin"
MOBILE=1 FILE_LOCAL="$WORK/m-$TS.bin" OUT="$WORK/out" SHOT="${SHOT3:-/tmp/webui_transfer_m.png}" \
  node "$ROOT/tests/webui_transfer.js" "http://$VM_IP:$PORT" || fail "mobile transfer failed"
[ "$(h "$WORK/out/dl-local.bin")" = "$(h "$WORK/m-$TS.bin")" ] || fail "mobile round-trip hash mismatch"
echo "   mobile OK"

echo "PASS: console e2e (stream + push/pull disk-verified + keyboard device-verified)"

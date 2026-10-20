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
HOLDPID=""
TUN=""
cleanup(){ [ -n "$TUN" ] && kill "$TUN" 2>/dev/null; TUN=""
  if [ -n "$HOLDPID" ]; then exec 8>&- 2>/dev/null; wait "$HOLDPID" 2>/dev/null; HOLDPID=""; fi
  rm -rf "$WORK"; ssh "$VM" "adb -s $SER shell am broadcast -a ADB_CLEAR_TEXT >/dev/null 2>&1; \
  adb -s $SER shell rm -f '/sdcard/Download/$PUSH' '/sdcard/Download/$UPA' >/dev/null 2>&1; \
  tmux kill-session -t $SESS 2>/dev/null; rm -rf '$ROOTDIR'" 2>/dev/null; }
fail(){ echo "FAIL: $*" >&2; cleanup; exit 1; }
trap cleanup EXIT
# (opens the self-chat via "me"; no name needed)

dump(){ ssh "$VM" "adb -s $SER shell uiautomator dump /sdcard/kcc.xml >/dev/null 2>&1; \
                   adb -s $SER shell cat /sdcard/kcc.xml 2>/dev/null"; }

echo "1) deploy + local root with a test file"
scp -q "$ROOT"/src/kakao_cli/{fs,webui,worker,cli,driver,adb,screen,scrcpy,consolelock,fmp4,video,errors}.py "$VM:$REMOTE/src/kakao_cli/" \
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

# Steps 2i–7 pin the PNG renderer (display 0 + adb input) so their device-pixel
# coordinates and the clipboard paste target are display 0; step 9 covers video.
PNGURL="http://$VM_IP:$PORT/?video=png"

echo "2i) IME regression (console keyboard capture, spied — nothing reaches the device)"
node "$ROOT/tests/webui_ime.js" "$PNGURL" || fail "IME regression failed"

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
  SHOT="${SHOT:-/tmp/webui_console.png}" node "$ROOT/tests/webui_console.js" "$PNGURL" \
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
  node "$ROOT/tests/webui_transfer.js" "$PNGURL" || fail "browser transfer driver failed"
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
  node "$ROOT/tests/webui_transfer.js" "$PNGURL" || fail "mobile transfer failed"
[ "$(h "$WORK/out/dl-local.bin")" = "$(h "$WORK/m-$TS.bin")" ] || fail "mobile round-trip hash mismatch"
echo "   mobile OK"

echo "7) clipboard (scrcpy): device→browser toast, Ctrl+V text/image → device"
scp -q "$ROOT/tests/clip_tool.py" "$VM:/tmp/clip_tool.py" || fail "scp clip_tool failed"
mkfifo "$WORK/hold"
ssh "$VM" "SER=$SER python3 /tmp/clip_tool.py hold" < "$WORK/hold" > "$WORK/hold.out" 2>&1 &
HOLDPID=$!; exec 8>"$WORK/hold"            # keep stdin open; closing it restores the clipboard
for i in $(seq 1 60); do grep -q READY "$WORK/hold.out" && break; sleep 1; done
grep -q READY "$WORK/hold.out" || fail "clipboard hold did not start"
ORIG_SHA=$(sed -n 's/^READY sha256=//p' "$WORK/hold.out")
echo "   original device clipboard held in memory (sha256 ${ORIG_SHA:0:12}…)"
ssh "$VM" "adb -s $SER shell am broadcast -a ADB_CLEAR_TEXT >/dev/null"
INFO=$(ssh "$VM" "cd $REMOTE && PYTHONPATH=src python3 -c \"
import json
from kakao_cli.adb import connect
from kakao_cli.driver import Driver
from kakao_cli import RID
d = Driver(connect('$SER')); d.open_self_chat()
s = d.screen(); m = [x for x in d._messages_detailed(s) if x.get('kind') != 'date']
ok = bool(m) and all(x.get('is_mine') for x in m)
t = [n for n in s.all() if n.rid.endswith('/message') and n.text.strip()]
inp = s.first(rid=RID['input'])
print(json.dumps({'ok': ok and bool(t) and inp is not None, 'expect': t[-1].text if t else '',
                  'bx': t[-1].center[0] if t else 0, 'by': t[-1].center[1] if t else 0,
                  'ix': inp.center[0] if inp else 0, 'iy': inp.center[1] if inp else 0}, ensure_ascii=False))
\"" | tail -1)
echo "$INFO" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin)['ok'] else 1)" \
  || fail "self-chat not confirmed for the clipboard test"
jget(){ echo "$INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['$1'])"; }
EXPECT=$(jget expect); BX=$(jget bx); BY=$(jget by); CIX=$(jget ix); CIY=$(jget iy)
PASTE="clip붙여넣기$TS"
python3 -c "import base64,sys; open(sys.argv[1],'wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHElEQVR4nGNgYGD4z4AKGEeFRoVGhUaFhq0QAFYAEQF9wh6pAAAAAElFTkSuQmCC'))" "$WORK/p.png"
EXPECT="$EXPECT" PASTE_TEXT="$PASTE" INPUT_X="$CIX" INPUT_Y="$CIY" PNG="$WORK/p.png" \
  node "$ROOT/tests/webui_clip.js" "$PNGURL" > "$WORK/clip.out" 2>&1 &
NPID=$!
for i in $(seq 1 120); do grep -q READY "$WORK/clip.out" && break; grep -q FAIL "$WORK/clip.out" && break; sleep 1; done
grep -q READY "$WORK/clip.out" || { cat "$WORK/clip.out"; fail "browser clip driver not ready"; }
# copy the newest self-chat message on the device: long-press → Copy
ssh "$VM" "cd $REMOTE && PYTHONPATH=src python3 -c \"
import time
from kakao_cli.adb import connect
from kakao_cli.driver import Driver
d = Driver(connect('$SER'))
d.dev.shell('input swipe $BX $BY $BX $BY 900'); time.sleep(1.2)
c = d._find(lambda s: next((n for n in s.all() if (n.text or n.desc) == 'Copy'), None), timeout=8)
assert c, 'Copy menu not found'
d.tap_node(c)
\"" || { kill $NPID; fail "device copy failed"; }
wait $NPID; rc=$?
grep -E "OK|sent|IMAGE_PATH|PASS|FAIL" "$WORK/clip.out" | sed 's/^/   /'
[ $rc = 0 ] || fail "browser clipboard driver failed"
got=0
for i in $(seq 1 8); do dump | grep -q "$PASTE" && { got=1; break; }; sleep 2; done
[ "$got" = 1 ] || fail "pasted text not in the message box"
echo "   Ctrl+V text landed in the message box (not sent)"
ssh "$VM" "adb -s $SER shell am broadcast -a ADB_CLEAR_TEXT >/dev/null"
sleep 1; dump | grep -q "$PASTE" && fail "message box not cleared"
echo "   message box cleared"
IMG=$(sed -n 's/^IMAGE_PATH //p' "$WORK/clip.out")
[ "$(ssh "$VM" "adb -s $SER shell sha256sum '$IMG'" | cut -d' ' -f1)" = "$(sha256sum "$WORK/p.png" | cut -d' ' -f1)" ] \
  || fail "pasted image hash mismatch on device"
ssh "$VM" "adb -s $SER shell rm -f '$IMG'"
echo "   pasted image uploaded to $IMG (sha256 OK, removed)"
exec 8>&-; wait "$HOLDPID"; HOLDPID=""
grep -q RESTORED "$WORK/hold.out" || fail "clipboard not restored"
NOW_SHA=$(ssh "$VM" "SER=$SER python3 /tmp/clip_tool.py hash" | sed -n 's/^sha256=\([0-9a-f]*\).*/\1/p')
[ "$NOW_SHA" = "$ORIG_SHA" ] || fail "device clipboard differs from the original after restore"
echo "   device clipboard restored (sha256 matches the original)"

echo "8) video: H.264 on a scrcpy virtual display (MSE on http, WebCodecs via localhost tunnel)"
scp -q "$ROOT/tests/vd_state.py" "$VM:/tmp/vd_state.py" || fail "scp vd_state failed"
export STATE_CMD="ssh $VM SER=$SER python3 /tmp/vd_state.py"
export CLI_CMD="ssh $VM 'cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli rooms --json --serial $SER'"
vrun(){ local out=$1; shift; "$@" > "$out" 2>&1; local rc=$?; cat "$out"; return $rc; }   # keep node's rc
vd_idle(){ for i in $(seq 1 30); do s=$($STATE_CMD); echo "$s" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d['kakao_display']==0 and not d['lock'] else 1)" \
  && return 0; sleep 1; done; echo "$s"; return 1; }
VSHOT="${VSHOT:-/tmp/webui_video}"
# keyboard (ADBKeyBoard) on the VD: the self-chat from step 7 moves along with the app
VKBD="vd키보드$TS"
CHECK_CMD="ssh $VM \"cd $REMOTE && PYTHONPATH=src python3 -c \\\"
import json
from kakao_cli.adb import connect
from kakao_cli.driver import Driver
from kakao_cli import RID
d = Driver(connect('$SER')); s = d.screen()   # dump only: it follows the virtual display
m = [x for x in d._messages_detailed(s) if x.get('kind') != 'date']
inp = s.first(rid=RID['input'])
print(json.dumps({'ok': bool(m) and all(x.get('is_mine') for x in m) and inp is not None, 'n': len(m),
                  'ix': inp.center[0] if inp is not None else 0, 'iy': inp.center[1] if inp is not None else 0}))
\\\"\"" \
DUMP_CMD="ssh $VM \"adb -s $SER shell uiautomator dump /sdcard/kcc.xml >/dev/null 2>&1; adb -s $SER shell cat /sdcard/kcc.xml\"" \
CLEAR_CMD="ssh $VM \"adb -s $SER shell am broadcast -a ADB_CLEAR_TEXT >/dev/null\"" \
KBD_TEXT="$VKBD" node "$ROOT/tests/webui_vd_keyboard.js" "http://$VM_IP:$PORT" || fail "keyboard on the virtual display failed"
vd_idle || fail "not back on display 0 after the VD keyboard check"
vrun "$WORK/v1.out" env SHOT="$VSHOT-desktop.png" node "$ROOT/tests/webui_video.js" "http://$VM_IP:$PORT" \
  || fail "video e2e (desktop, MSE) failed"
vd_idle || fail "KakaoTalk not back on display 0 / lock not cleared after the viewer left"
echo "   viewer gone → VD destroyed, KakaoTalk back on display 0, lock cleared"
TPORT=$((PORT + 10000))
ssh -N -L "$TPORT:127.0.0.1:$PORT" "$VM" & TUN=$!
sleep 2
vrun "$WORK/v2.out" env RENDER=wc node "$ROOT/tests/webui_video.js" "http://localhost:$TPORT" \
  || { kill $TUN; fail "video e2e (desktop, WebCodecs) failed"; }
kill $TUN 2>/dev/null; TUN=""
vd_idle || fail "not back on display 0 after the WebCodecs viewer"
vrun "$WORK/v3.out" env MOBILE=1 SHOT="$VSHOT-mobile.png" node "$ROOT/tests/webui_video.js" "http://$VM_IP:$PORT" \
  || fail "video e2e (mobile) failed"
vd_idle || fail "not back on display 0 after the mobile viewer"
ssh "$VM" "cd $REMOTE && PYTHONPATH=src python3 -m kakao_cli doctor --json --serial $SER" \
  | grep -q '"web_console_virtual_display": null' || fail "doctor still reports the console lock"
grep -h METRIC "$WORK"/v*.out | sed 's/^/   /'
echo "   video OK (MSE + WebCodecs + mobile, flex resize, tap reflection, mirror toggle, PNG fallback, CLI lock)"

echo "9) stopping webui (SIGHUP via tmux) leaves nothing on the device"
ssh "$VM" "tmux kill-session -t $SESS 2>/dev/null"; sleep 6
left=$(ssh "$VM" "adb -s $SER forward --list | grep -c scrcpy_; adb -s $SER shell 'ps -A -o ARGS' | grep -c com.genymobile.scrcp[y]; adb -s $SER shell ls /data/local/tmp/ | grep -c kakao-cli-scrcpy")
[ "$(echo $left | tr -d ' ')" = "000" ] || fail "device not cleaned after stop (forwards/procs/jar: $left)"
echo "   no forward, no scrcpy process, no jar"

echo "PASS: console e2e (stream + push/pull disk-verified + keyboard device-verified + transfers + clipboard + video)"

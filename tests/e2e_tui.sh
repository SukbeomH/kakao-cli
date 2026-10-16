#!/usr/bin/env bash
# End-to-end TUI test. Needs a real device + a PTY (tmux), so run it ON the
# Android host (VM), not in CI. Drives `kakao tui` and asserts a Korean message
# reaches the self-chat (verified independently via adb readback).
#
#   tests/e2e_tui.sh [SERIAL]
set -u
SER="${1:-127.0.0.1:5555}"
SESS="kttest_$$"
MSG="e2e-tui-$(date +%H%M%S)-한글"
ADB="adb -s $SER"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup(){ tmux kill-session -t "$SESS" 2>/dev/null; }
trap cleanup EXIT
fail(){ echo "FAIL: $*" >&2; exit 1; }

wait_for(){ # wait_for <timeout_s> <pattern>
  local t=$1 pat=$2 i
  for ((i=0; i<t; i+=2)); do
    tmux capture-pane -t "$SESS" -p 2>/dev/null | grep -q "$pat" && return 0
    sleep 2
  done
  return 1
}

echo "1) launch TUI"
tmux new-session -d -s "$SESS" -x 120 -y 30
tmux send-keys -t "$SESS" "cd $ROOT && PYTHONPATH=src python3 -m kakao_cli tui --serial $SER" Enter

echo "2) wait for rooms"
wait_for 150 "홍석범" || fail "rooms did not load"

echo "3) locate self-chat row and navigate to it"
# left column holds room names in order; find 홍석범's 0-based row index
idx=$(tmux capture-pane -t "$SESS" -p | sed -n '2,28p' | cut -c1-40 \
      | grep -n '홍석범' | head -1 | cut -d: -f1)
[ -n "$idx" ] || fail "self-chat row not found in list"
idx=$((idx - 1))           # grep is 1-based; selection starts at row 0
echo "   self-chat at row $idx"
for ((k=0; k<idx; k++)); do tmux send-keys -t "$SESS" Down; sleep 0.4; done
tmux send-keys -t "$SESS" C-m
wait_for 70 "in 홍석범" || fail "self-chat did not open"
sleep 4

echo "4) type Korean message and send"
tmux send-keys -t "$SESS" "$MSG"; sleep 2
tmux capture-pane -t "$SESS" -p | grep -q "$MSG" || fail "input did not capture the message"
tmux send-keys -t "$SESS" C-m

echo "5) assert sent (TUI status)"
wait_for 20 "sent (verified)" || echo "WARN: TUI status not seen (checking device anyway)"

echo "6) assert delivered (device readback)"
ok=0
for i in $(seq 1 8); do
  $ADB shell uiautomator dump /sdcard/e2e.xml >/dev/null 2>&1
  if $ADB shell cat /sdcard/e2e.xml 2>/dev/null | grep -q "$MSG"; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] || fail "message '$MSG' not found in self-chat"

echo "7) quit"
tmux send-keys -t "$SESS" Escape; sleep 1; tmux send-keys -t "$SESS" "q"; sleep 2

echo "PASS: TUI e2e (sent & verified '$MSG')"

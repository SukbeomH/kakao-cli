#!/usr/bin/env bash
# Boot-time bring-up for the KakaoTalk redroid substrate (run as root by systemd).
# Ensures binder, mounts binderfs, starts the container, launches KakaoTalk (login
# persists in the /data volume, so no re-login). Idempotent.
set -u
SER=127.0.0.1:5555
log(){ echo "[kakao-boot] $*"; }

# 1. binder kernel module (reinstall modules-extra if a kernel update removed it)
if ! modprobe binder_linux devices="binder,hwbinder,vndbinder" 2>/dev/null; then
  log "binder module missing for $(uname -r); installing modules-extra"
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "linux-modules-extra-$(uname -r)"
  modprobe binder_linux devices="binder,hwbinder,vndbinder"
fi

# 2. binderfs
mkdir -p /dev/binderfs
mountpoint -q /dev/binderfs || mount -t binder none /dev/binderfs

# 3. container (restart policy is 'no'; systemd owns it)
docker start redroid || docker restart redroid

# 4. wait for Android, launch app (retry past the cold-start segfault)
adb connect "$SER" >/dev/null 2>&1
for i in $(seq 1 60); do
  [ "$(adb -s "$SER" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break
  sleep 5
done
for t in 1 2 3; do
  adb -s "$SER" shell am start -n com.kakao.talk/.activity.SplashActivity >/dev/null 2>&1
  sleep 8
  a=$(adb -s "$SER" shell dumpsys activity activities 2>/dev/null | grep -m1 topResumedActivity | grep -oE 'com.kakao.talk/[^ }]+')
  [ -n "$a" ] && { log "app foreground: $a"; break; }
done
# ensure runtime permissions used by automation (notifications + media send/recv)
for P in POST_NOTIFICATIONS READ_MEDIA_IMAGES READ_MEDIA_VIDEO READ_MEDIA_VISUAL_USER_SELECTED READ_CONTACTS; do
  adb -s "$SER" shell pm grant com.kakao.talk android.permission.$P >/dev/null 2>&1
done
# ensure ADBKeyBoard IME (persists in /data, but re-assert defensively)
adb -s "$SER" shell ime enable com.android.adbkeyboard/.AdbIME >/dev/null 2>&1
adb -s "$SER" shell ime set com.android.adbkeyboard/.AdbIME >/dev/null 2>&1
# background the app so incoming messages post notifications (receive-ready)
adb -s "$SER" shell input keyevent KEYCODE_HOME >/dev/null 2>&1
log "bring-up complete"

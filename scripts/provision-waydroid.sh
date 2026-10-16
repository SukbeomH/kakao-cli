#!/usr/bin/env bash
# Provision Waydroid + KakaoTalk on the Ubuntu VM (108, 192.168.0.30).
# Run ON the guest via SSH as user `agent` (has sudo). Idempotent-ish; re-runnable.
#
# STATUS: draft. Untested until VM 108 boots (needs the one-time root disk import
# on the PVE host). Expect live tuning — headless Wayland + ARM translation for a
# hardened app are the fragile parts. See docs/initiatives/2026-10-16-design.md.
set -euo pipefail

log(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

DEVICE_PROFILE="${DEVICE_PROFILE:-galaxy-tab-s9}"   # see props block below

log "1. base packages"
sudo apt-get update -y
sudo apt-get install -y curl ca-certificates python3 python3-venv git \
    android-tools-adb weston xwayland lxc

log "2. Waydroid repo + install"
if ! command -v waydroid >/dev/null; then
  curl -fsSL https://repo.waydro.id | sudo bash
  sudo apt-get install -y waydroid
fi

log "3. waydroid init (GAPPS) — only if not initialised"
if [ ! -f /var/lib/waydroid/waydroid.cfg ]; then
  sudo waydroid init -s GAPPS -f
fi

log "4. ARM translation (libndk) + GApps via waydroid_script"
# KakaoTalk ships ARM-only .so; x86_64 needs a translation layer.
if [ ! -d "$HOME/waydroid_script" ]; then
  git clone https://github.com/casualsnek/waydroid_script "$HOME/waydroid_script"
fi
cd "$HOME/waydroid_script"
python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt
# libndk works on AMD/Intel; libhoudini is the Intel alternative.
sudo ./venv/bin/python3 main.py install libndk || sudo ./venv/bin/python3 main.py install libhoudini
cd "$HOME"

log "5. tablet device identity (coexist with phone) — profile: $DEVICE_PROFILE"
# Values are set into waydroid_base.prop so they survive restarts.
BASEPROP=/var/lib/waydroid/waydroid_base.prop
case "$DEVICE_PROFILE" in
  galaxy-tab-s9)
    # Samsung Galaxy Tab S9 Wi-Fi (SM-X710 / gts9wifi), Android 14. Values verified
    # against the AndroidDumps firmware dump (docs/initiatives/2026-10-16-tablet-fingerprint.md).
    PROPS=(
      "ro.product.manufacturer=samsung"
      "ro.product.brand=samsung"
      "ro.product.model=SM-X710"
      "ro.product.name=gts9wifixx"
      "ro.product.device=gts9wifi"
      "ro.build.characteristics=tablet"
      "ro.build.version.release=14"
      "ro.build.version.sdk=34"
      "ro.build.id=UP1A.231005.007"
      "ro.build.version.incremental=X710XXU5BYA1"
      "ro.build.description=gts9wifixx-user 14 UP1A.231005.007 X710XXU5BYA1 release-keys"
      "ro.build.fingerprint=samsung/gts9wifixx/gts9wifi:14/UP1A.231005.007/X710XXU5BYA1:user/release-keys"
      "ro.sf.lcd_density=340"
    )
    # 1600x2560 @ 340dpi -> smallestWidthDp ~= 753dp (>=600 tablet). Partition-specific
    # ro.product.{system,vendor,odm}.* should also be aligned; keep ro.product.system.device=qssi.
    WIDTH=1600; HEIGHT=2560; DENSITY=340
    ;;
  *) echo "unknown DEVICE_PROFILE=$DEVICE_PROFILE" >&2; exit 2 ;;
esac
sudo touch "$BASEPROP"
for p in "${PROPS[@]}"; do
  k=${p%%=*}
  sudo sed -i "\#^$k=#d" "$BASEPROP"
  echo "$p" | sudo tee -a "$BASEPROP" >/dev/null
done
sudo waydroid prop set persist.waydroid.width "$WIDTH"  || true
sudo waydroid prop set persist.waydroid.height "$HEIGHT" || true

log "6. start container + headless session"
sudo systemctl enable --now waydroid-container
export XDG_RUNTIME_DIR=/run/user/$(id -u); mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
if ! pgrep -f "weston.*headless" >/dev/null; then
  weston --backend=headless-backend.so --socket=wayland-1 --idle-time=0 >/tmp/weston.log 2>&1 &
  sleep 3
fi
export WAYLAND_DISPLAY=wayland-1
waydroid session stop >/dev/null 2>&1 || true
waydroid session start >/tmp/waydroid-session.log 2>&1 &
sleep 20
waydroid status || true

log "7. adb over TCP (for the kakao-cli control host)"
# Waydroid exposes adb on 127.0.0.1:5555 inside the VM; relay to the LAN.
adb connect 127.0.0.1:5555 || true
# Expose to control host: run `kakao` with --connect 192.168.0.30:5555 after setting up
# an adbd tcp or socat relay (see docs). Keeping localhost-only by default for safety.

log "8. install KakaoTalk"
echo "Install via Play Store (GApps, sign in to Google first) OR sideload the collected splits:"
echo "  adb install-multiple base.apk split_config.arm64_v8a.apk split_config.ko.apk split_config.xxhdpi.apk"

log "done — verify: waydroid status; adb devices; adb shell dumpsys package com.kakao.talk | grep versionName"

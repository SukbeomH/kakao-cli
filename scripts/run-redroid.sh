#!/usr/bin/env bash
# Run the official KakaoTalk Android substrate as a redroid (Docker) container.
# Run ON the Linux host whose kernel has binder (here: VM 108, 192.168.0.30).
# redroid images already bundle libndk_translation (ARM apps on x86_64).
# Tablet identity is passed as trailing Android props so KakaoTalk treats this as a
# Galaxy Tab companion device (coexists with the phone). See design.md §3.1/§8.6.
set -euo pipefail

IMAGE="${IMAGE:-redroid/redroid:14.0.0-latest}"   # Android 14 == SM-X710 fingerprint
DATA="${DATA:-/opt/redroid/data}"
NAME="${NAME:-redroid}"

sudo mkdir -p "$DATA"
sudo docker rm -f "$NAME" >/dev/null 2>&1 || true
sudo docker run -itd --name "$NAME" --privileged --restart unless-stopped \
  -v "$DATA:/data" -p 5555:5555 \
  "$IMAGE" \
  androidboot.redroid_width=1600 \
  androidboot.redroid_height=2560 \
  androidboot.redroid_dpi=340 \
  androidboot.redroid_gpu_mode=guest \
  ro.product.manufacturer=samsung \
  ro.product.brand=samsung \
  ro.product.model=SM-X710 \
  ro.product.name=gts9wifixx \
  ro.product.device=gts9wifi \
  ro.build.characteristics=tablet

echo "waiting for boot..."; sleep 20
adb connect 127.0.0.1:5555 || true
adb -s 127.0.0.1:5555 wait-for-device
adb -s 127.0.0.1:5555 shell getprop sys.boot_completed
adb -s 127.0.0.1:5555 shell getprop ro.product.model
echo "sideload KakaoTalk splits:"
echo "  adb -s 127.0.0.1:5555 install-multiple base.apk split_config.arm64_v8a.apk split_config.ko.apk split_config.xxhdpi.apk"

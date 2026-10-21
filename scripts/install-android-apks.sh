#!/usr/bin/env bash
# Lima Linux guest only. Called explicitly by setup-macos.sh; no login or messages.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_APK_DIR="${1:-/tmp/kakao-apks}"
TASK_SERIAL=127.0.0.1:5555
cd "$TASK_ROOT"
adb connect "$TASK_SERIAL" >/dev/null
TASK_INSTALL_TALK=true
TASK_INSTALL_IME=true
adb -s "$TASK_SERIAL" shell pm path com.kakao.talk | grep -q '^package:' && TASK_INSTALL_TALK=false
adb -s "$TASK_SERIAL" shell pm path com.android.adbkeyboard | grep -q '^package:' && TASK_INSTALL_IME=false
if [[ "$TASK_INSTALL_TALK" == false && "$TASK_INSTALL_IME" == false ]]; then
  echo '기존 앱을 유지합니다(APK 재설치·업데이트 없음).'
  exit 0
fi
if ! command -v apksigner >/dev/null || ! command -v java >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y apksigner default-jre-headless
fi
python3 scripts/download-apks.py "$TASK_APK_DIR"
# The downloader checks archive/split hashes; apksigner additionally verifies signatures.
TASK_TALK_CERT="$(python3 -c 'import json; print(json.load(open("deploy/apks.json"))["kakaotalk"]["certificate_sha256"])')"
TASK_IME_CERT="$(python3 -c 'import json; print(json.load(open("deploy/apks.json"))["adbkeyboard"]["certificate_sha256"])')"
for apk in com.kakao.talk.apk config.arm64_v8a.apk config.en.apk config.mdpi.apk ADBKeyboard.apk; do
  TASK_CERT="$TASK_TALK_CERT"
  [[ "$apk" != ADBKeyboard.apk ]] || TASK_CERT="$TASK_IME_CERT"
  TASK_SIGNATURE="$(apksigner verify --print-certs "$TASK_APK_DIR/$apk")"
  printf '%s\n' "$TASK_SIGNATURE" | grep -Fxq "Signer #1 certificate SHA-256 digest: $TASK_CERT" \
    || { echo "서명 인증서 불일치: $apk" >&2; exit 1; }
done
if [[ "$TASK_INSTALL_TALK" == true ]]; then
  adb -s "$TASK_SERIAL" install-multiple "$TASK_APK_DIR/com.kakao.talk.apk" \
    "$TASK_APK_DIR/config.arm64_v8a.apk" "$TASK_APK_DIR/config.en.apk" "$TASK_APK_DIR/config.mdpi.apk"
fi
if [[ "$TASK_INSTALL_IME" == true ]]; then
  adb -s "$TASK_SERIAL" install "$TASK_APK_DIR/ADBKeyboard.apk"
fi

#!/usr/bin/env bash
# 새 compose 환경용 1회 부트스트랩. 컨테이너 생성/재시작·메시지 전송 없음.
set -euo pipefail
SER="${KAKAO_ADB_TARGET:-redroid:5555}"
ready=false
for ((attempt=0; attempt<60; attempt++)); do
  adb connect "$SER" >/dev/null 2>&1 || true
  if [[ "$(adb -s "$SER" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then
    ready=true
    break
  fi
  sleep 5
done
if [[ "$ready" != true ]]; then
  echo 'Android 부팅 또는 ADB 인증 실패' >&2
  exit 1
fi
if ! adb -s "$SER" shell pm path com.kakao.talk | tr -d '\r' | grep -q '^package:'; then
  echo '공식 카카오톡 APK를 먼저 설치하세요' >&2
  exit 1
fi
for permission in POST_NOTIFICATIONS READ_MEDIA_IMAGES READ_MEDIA_VIDEO READ_MEDIA_VISUAL_USER_SELECTED READ_CONTACTS; do
  adb -s "$SER" shell pm grant com.kakao.talk "android.permission.$permission" >/dev/null 2>&1 || true
done
if ! adb -s "$SER" shell ime list -s | tr -d '\r' | grep -qx 'com.android.adbkeyboard/.AdbIME'; then
  echo 'ADBKeyBoard APK를 먼저 설치하세요' >&2
  exit 1
fi
adb -s "$SER" shell ime enable com.android.adbkeyboard/.AdbIME >/dev/null
adb -s "$SER" shell ime set com.android.adbkeyboard/.AdbIME >/dev/null
launched=false
for ((attempt=0; attempt<3; attempt++)); do
  adb -s "$SER" shell am start -n com.kakao.talk/.activity.SplashActivity >/dev/null 2>&1 || true
  sleep 8
  if adb -s "$SER" shell dumpsys activity activities | grep -m1 topResumedActivity | grep -q 'com.kakao.talk/'; then
    launched=true
    break
  fi
done
if [[ "$launched" != true ]]; then
  echo '카카오톡 실행 확인 실패' >&2
  exit 1
fi
adb -s "$SER" shell input keyevent KEYCODE_HOME >/dev/null
printf '%s\n' '카카오톡 실행·권한·IME 준비 완료 (로그인 상태는 콘솔에서 확인)'

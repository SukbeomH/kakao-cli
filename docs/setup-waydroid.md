# Waydroid 환경 구축 가이드 (Proxmox VM/CT)

kakao-cli가 제어할 Android 런타임을 세운다. 목표: **허용 갤럭시탭으로 인식되는 Waydroid**에 공식 카카오톡을 올리고, 개발 호스트에서 **adb(TCP)**로 접속.

> 이 문서의 명령은 템플릿이다. 커널·배포판·Waydroid 버전에 따라 조정이 필요하고, ★표시는 **실측 확정 지점**이다.

## 1. 왜 VM/CT가 별도로 필요한가
개발 샌드박스(비특권 LXC)는 `/dev/binder`가 없어 Waydroid가 안 뜬다. Waydroid는 binder가 필요하므로:
- **권장: 전용 VM**(KVM). 커널 모듈 자유롭게 로드, GPU/소프트렌더 유연. 가장 말썽 적음.
- 대안: **특권 LXC CT** + 호스트 커널에 binder 모듈 + 디바이스 전달(설정 까다로움).

## 2. 호스트/게스트 커널 — binder
```bash
# 게스트(또는 binder 제공 주체)에서
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"   # 커널에 있을 때
ls -l /dev/binder*            # ★ 세 노드가 보여야 함
```
커널에 binder_linux가 없으면 `binder_linux` DKMS(anbox/waydroid) 설치 또는 binder 내장 커널 사용.

## 3. Waydroid 설치 + 이미지
```bash
sudo apt install waydroid       # 또는 배포판별 패키지/스크립트
sudo waydroid init -s GAPPS     # Play 로그인 필요 → GAPPS 이미지
sudo systemctl enable --now waydroid-container
```
- x86_64 호스트: 카카오톡이 **ARM 전용 so**라 **ARM 변환 레이어 필수**(libndk_translation 또는 libhoudini). waydroid-extras/스크립트로 설치 후 재시작. ★변환 없이는 앱이 네이티브 로드 실패로 죽는다.
- GApps 사용 시 Play 인증(device registration)을 한 번 거쳐야 Play에서 설치 가능.

## 4. 태블릿(서브기기) 정체성 스푸핑 ★핵심
폰과 **동시 로그인**하려면 카카오톡이 태블릿/서브기기로 인식해야 한다(§design 3.1).
```bash
# 디스플레이를 태블릿 크기/밀도로 (sw600dp+). 예시값, 모델에 맞게 ★
waydroid prop set persist.waydroid.width 1600
waydroid prop set persist.waydroid.height 2560
# 기기 정체성 → 허용 갤럭시탭으로. 예시는 자리표시자, 실제 허용 모델 지문으로 교체 ★
waydroid prop set ro.product.manufacturer samsung
waydroid prop set ro.product.model SM-X900
waydroid prop set ro.product.device <device-codename>
waydroid prop set ro.build.characteristics tablet
waydroid prop set ro.build.fingerprint <matching-fingerprint>
sudo systemctl restart waydroid-container
```
- resetprop(Magisk)로 더 깊은 속성까지 바꿔야 할 수도 있다(일부 prop은 런타임 고정).
- ★**검증**: 카카오톡 로그인 화면에 **"다른 기기와 함께 사용"** 옵션이 떠야 한다. 안 뜨면 **폰 로그인 금지**(기존 폰 데이터 손실 위험) → 다른 허용 모델 지문으로 재시도.
- 허용 모델 목록은 시기별로 바뀌므로 CLI 설정(`device_profile`)로 외부화, 하드코딩 금지.

## 5. 카카오톡 설치
- Play(GApps)에서 설치가 정석(자동 업데이트·정상 서명). 수집해 둔 APK(`com.kakao.talk 26.8.3`)로 사이드로드도 가능하나 split APK라 `adb install-multiple` 필요.
```bash
adb install-multiple base.apk split_config.arm64_v8a.apk split_config.ko.apk split_config.xxhdpi.apk
```

## 6. adb(TCP) 노출 → 개발 호스트에서 접속
```bash
# 게스트(Waydroid 세션 안)
adb tcpip 5555            # 또는 설정에서 네트워크 adb 허용
# 개발 호스트
adb connect <vm-ip>:5555
adb devices               # ★ device 상태 확인
```
보안: adb 포트는 **신뢰 네트워크/방화벽 뒤**에만. 전역 노출 금지.

## 7. 스모크 테스트
```bash
adb shell dumpsys package com.kakao.talk | grep versionName   # 설치 확인
adb shell monkey -p com.kakao.talk -c android.intent.category.LAUNCHER 1  # 기동
kakao read me --source ui  # UI 트리는 /dev/tty 표준출력으로 메모리에서만 처리한다
# kakao-cli
uv run kakao doctor
```

## 8. 체크리스트
- [ ] `/dev/binder*` 3노드
- [ ] Waydroid 세션 기동, GApps Play 인증
- [ ] ARM 변환 레이어로 카카오톡 크래시 없이 기동
- [ ] 태블릿 지문 + "다른 기기와 함께 사용" 옵션 노출 ★
- [ ] 폰 로그아웃 없이 로그인 성공
- [ ] 개발 호스트에서 `adb connect` + `uiautomator dump` 성공

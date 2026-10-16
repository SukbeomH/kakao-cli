# 카카오톡 태블릿 공존 로그인용 갤럭시탭 지문(fingerprint) 조사

작성 2026-10-16. 목표: Waydroid를 카카오톡이 "다른 기기와 함께 사용"(서브기기) 옵션을 주는
갤럭시탭으로 인식시키기 위한 Android build 속성을 실제 펌웨어 덤프 기반으로 확정한다.

## 1. 추천 모델: Galaxy Tab S9 Wi-Fi = **SM-X710** (코드네임 gts9wifi)

### 선정 근거
- **최신성**: 2023-08 출시, SM-X 시리즈. 조사 시점 기준 펌웨어가 Android 14(One UI 6.0)까지 올라가 있어
  최신 카카오톡의 minSdk/타깃 요건을 여유 있게 만족한다.
- **Wi-Fi 전용 변종**이라 셀룰러/IMEI 없는 Waydroid 환경과 모순이 적다(5G 변종 SM-X716B = gts9 대비 유리).
- **검증 가능한 펌웨어 덤프 존재**: AndroidDumps(gitlab `dumps.tadiphone.dev`)에 gts9wifi 전체 build.prop이
  공개돼 있어 모든 속성을 실측 인용할 수 있다. (아래 §2 전부 이 덤프에서 복사)
- **사용자 보고(모델 계열)**: 커뮤니티에서 카카오톡 서브기기 스푸핑에 쓰는 모델명으로 SM-X700(Tab S8 Wi-Fi)이
  반복적으로 언급된다(디시 태블릿갤 App Cloner 글, 하모니카/클리앙 build.prop 글). SM-X710은 그 바로 다음 세대
  동일 라인(SM-X70x → SM-X71x)이라 allowlist 포함 가능성이 높다. 단, **SM-X710 자체로 카카오톡 옵션이 떴다는
  직접 후기는 확인하지 못함(미검증)** — allowlist는 서버 판정이므로 실기동 로그인 화면에서 최종 확인 필요.
  - 안전장치: 만약 SM-X710에서 옵션이 안 뜨면 **SM-X700(Tab S8 Wi-Fi, gts8wifi)** 로 교체(커뮤니티 보고 모델).
    단 이 모델은 본 조사에서 공개 덤프의 전체 build.prop까지는 확보하지 못했다. → 1순위는 덤프가 확실한 SM-X710.
  - 주의: 선행조사 dex에서 발견된 `SM-T230`/`SM-T580`/`SM-X900` 문자열은 각각 2014/2016년 구형 Tab 4·Tab A와
    Tab S8 Ultra로 세대가 제각각이라 **공존 로그인 allowlist라기보다 기기별 예외/워크어라운드 목록 형태**로 보인다.
    용도 미확인 → allowlist 근거로 쓰지 않는다(옵션 노출은 서버 판정이며 클라이언트에 목록 분기 없음, §4).

## 2. 설정할 Android build 속성 (SM-X710, Android 14)

출처: AndroidDumps `dumps/samsung/gts9wifi`, 브랜치
`gts9wifixx-user-14-UP1A.231005.007-X710XXU5BYA1-release-keys`
(https://dumps.tadiphone.dev/dumps/samsung/gts9wifi). 아래 값은 해당 브랜치의
`system/system/build.prop`, `vendor/build.prop`, `odm/etc/build.prop`에서 직접 grep한 것.

| 속성 | 값 | 검증 상태 | 출처 |
|---|---|---|---|
| ro.product.manufacturer | samsung | 검증 | system/system/build.prop (`ro.product.system.manufacturer=samsung`) |
| ro.product.brand | samsung | 검증 | 위와 동일 |
| ro.product.model | SM-X710 | 검증 | `ro.product.system.model` / `ro.product.vendor.model` = SM-X710 |
| ro.product.name | gts9wifixx | 검증 | `ro.product.system.name` / `ro.product.vendor.name` = gts9wifixx |
| ro.product.device | gts9wifi | 검증 | `ro.product.vendor.device` / `ro.product.odm.device` = gts9wifi |
| ro.build.version.release | 14 | 검증 | `ro.build.version.release=14` |
| ro.build.version.sdk | 34 | 검증 | `ro.build.version.sdk=34` |
| ro.build.id | UP1A.231005.007 | 검증 | `ro.build.id=UP1A.231005.007` |
| ro.build.display.id | UP1A.231005.007.X710XXU5BYA1 | 검증 | `ro.build.display.id=...` |
| ro.build.version.incremental | X710XXU5BYA1 | 검증 | `ro.build.version.incremental=X710XXU5BYA1` |
| ro.build.version.security_patch | 2024-12-01 | 검증 | `ro.build.version.security_patch=2024-12-01` |
| ro.build.description | gts9wifixx-user 14 UP1A.231005.007 X710XXU5BYA1 release-keys | 검증 | `ro.build.description=...` |
| ro.build.characteristics | tablet | 검증 | `ro.build.characteristics=tablet` |
| ro.sf.lcd_density | 340 | 검증 | `ro.sf.lcd_density=340` (vendor/build.prop) |
| ro.build.tags | release-keys | 검증(지문에 포함) | fingerprint 꼬리 `:user/release-keys` |
| ro.build.type | user | 검증(지문에 포함) | fingerprint `...:user/...` |

### 파티션별 ro.product.* 변종도 함께 설정 (검증, 덤프에서 직접 확인)
탐지 코드가 top-level 대신 `ro.product.vendor.model` 등을 직접 읽으면 Waydroid 기본값이 노출된다.
아래를 모두 맞춰야 한다. (`ro.product.system.device=qssi` 는 실기기 정상값 — gts9wifi로 "수정"하지 말 것)

```
ro.product.system.brand=samsung        ro.product.vendor.brand=samsung        ro.product.odm.brand=samsung
ro.product.system.manufacturer=samsung ro.product.vendor.manufacturer=samsung ro.product.odm.manufacturer=samsung
ro.product.system.model=SM-X710        ro.product.vendor.model=SM-X710        ro.product.odm.model=SM-X710
ro.product.system.name=gts9wifixx      ro.product.vendor.name=gts9wifixx      ro.product.odm.name=gts9wifixx
ro.product.system.device=qssi          ro.product.vendor.device=gts9wifi      ro.product.odm.device=gts9wifi
```

### ro.build.fingerprint (중요 — 앱이 Build.FINGERPRINT로 읽는 값)
덤프의 각 파티션 지문은 다음과 같다(모두 검증):

- `ro.system.build.fingerprint`  = `samsung/gts9wifixx/qssi:14/UP1A.231005.007/X710XXU5BYA1:user/release-keys`
- `ro.product.build.fingerprint` = `samsung/gts9wifixx/qssi:14/UP1A.231005.007/X710XXU5BYA1:user/release-keys`
- `ro.vendor.build.fingerprint`  = `samsung/gts9wifixx/gts9wifi:13/TP1A.220624.014/X710XXU5BYA1:user/release-keys`

덤프 build.prop에 **bare `ro.build.fingerprint=` 라인이 어느 파티션에도 없다**. 이 경우 Android init이
`ro.build.fingerprint`를 구성요소 속성에서 **기계적으로 파생**한다:
`brand/name/device:release/id/incremental:type/tags`
(AOSP `system/core/init/property_service.cpp`의 `property_derive_build_fingerprint()`;
https://cs.android.com/android/platform/superproject/+/master:system/core/init/property_service.cpp).
따라서 실기기 `getprop ro.build.fingerprint` 가 반환하는 값은 아래로 결정된다:

```
samsung/gts9wifixx/gts9wifi:14/UP1A.231005.007/X710XXU5BYA1:user/release-keys
```

- **추론이 아니라 AOSP init 파생 규칙으로 결정된 값** — 구성요소(brand=samsung, name=gts9wifixx,
  device=gts9wifi, release=14, id=UP1A.231005.007, incremental=X710XXU5BYA1, type=user, tags=release-keys)는
  전부 덤프에서 검증됨.
- **Waydroid 함의**: 이 bare 라인을 명시하지 않으면 init이 Waydroid 자체 이미지 값에서 파생한다. 따라서
  `ro.build.fingerprint`를 위 값으로 **명시 설정**해야 한다(구성 속성만 바꾸고 방치하면 안 됨).
- 대안(완전 검증값 그대로 쓰기): system/product 지문 `.../qssi:14/...` 를 그대로 넣어도 되나 device 슬롯이
  `qssi`(GSI 프로젝트명)라 ro.product.device=gts9wifi와 불일치 → 일관성 위해 `gts9wifi` 슬롯 파생본을 권장.

### 디스플레이
- 해상도: **1600 x 2560 px**(11.0" WQXGA). 검증: GSMArena Galaxy Tab S9 사양
  (https://www.gsmarena.com/samsung_galaxy_tab_s9-12439.php).
- 밀도: **340 dpi**. 검증: 덤프 `ro.sf.lcd_density=340`.
- smallestScreenWidthDp = 1600 / (340/160) ≈ **753 dp** (>= 600 만족, 태블릿 레이아웃 확정).
- Waydroid 권장 설정: `waydroid prop set persist.waydroid.width 1600`,
  `persist.waydroid.height 2560`, 밀도 340. (가로/세로는 Waydroid 창 방향에 맞게 조정)

## 3. Waydroid 적용 위치(참고, 변경 금지 — 구현 단계에서)

- `/var/lib/waydroid/waydroid_base.prop` 또는 `waydroid.cfg [properties]` 섹션에 위 ro.* 키를 추가하면
  컨테이너 build.prop에 반영됨.
  - 출처: Waydroid 공식 문서 "Waydroid Prop Options"(https://docs.waydro.id/usage/waydroid-prop-options),
    Quackdoc/waydroid-scripts spoof-device.sh
    (https://github.com/Quackdoc/waydroid-scripts/blob/main/spoof-device.sh).
- ro.build.characteristics=tablet 와 밀도/해상도까지 함께 맞춰야 "다른 기기와 함께 사용"이 뜰 가능성이 높아진다
  (선행조사 design.md §3.1 결론과 일치).

## 4. 카카오톡 탐지 / allowlist 집행 위치

### allowlist는 서버 판정(server-side)으로 추정 — 본 저장소 선행조사 근거
- `2026-10-16-prior-art.md`(dex 분석): 카카오톡 APK dex에 `sub_device`/`subDevice`/`SUB_DEVICE`,
  `isTablet`, `deviceType`, `useSub`, `companion` 심볼과 모델 문자열 `SM-T230`/`SM-T580`/`SM-X900`가 존재.
  반면 **"다른 기기와 함께" UI 문구/분기 로직은 클라이언트 코드에 없음** → 옵션 노출 여부를 서버가 내려준다고 판단.
- 함의: build.prop만 바꾼다고 로컬에서 토글로 확인 불가. **서버가 모델/서브기기 자격을 재검증**하므로
  로그인 플로우를 실제로 태워 옵션 표출 여부로만 확정 가능. 모델이 allowlist에 없으면 스푸핑해도 옵션 안 뜸.
- dex에 SM-X900(Tab S8 Ultra)이 하드코딩돼 있는 점은 allowlist에 SM-X 세대가 포함됨을 시사(SM-X710도 포함 기대).

### 에뮬레이터/루트 탐지 리스크 (Waydroid 특이사항)
- **ARM 전용 네이티브 라이브러리**: 카카오톡 APK는 arm64-v8a/armeabi-v7a만 포함(prior-art). x86_64 Waydroid에서
  구동하려면 libndk_translation/libhoudini 변환 레이어 필수 — 미설치 시 앱 자체가 뜨지 않음(탐지 이전 문제).
- **Play Integrity / SafetyNet**: 카카오톡 로그인·기기등록에서 무결성 토큰을 요구할 수 있음. Waydroid는 기본
  미인증(ro.boot.verifiedbootstate, basic integrity 실패) → 서브기기 등록이 막힐 수 있음. 필요 시 GApps +
  무결성 우회(예: PlayIntegrityFix 류) 검토. (본 조사에서 카카오톡의 Integrity 강제 여부는 미검증)
- **빌드 지문 일관성**: ro.product.device, ro.build.fingerprint device 슬롯, ro.product.name 을 서로
  모순 없이 맞춰야 함(위 §2의 gts9wifi 일관본 권장). `ro.build.tags=test-keys`나 `ro.debuggable=1` 노출은
  루팅/에뮬 신호가 되므로 release-keys 유지, 디버그 플래그 정리 권장.
- **일반 루트 탐지**: su/magisk 바이너리, 에뮬레이터 특유 속성(ro.kernel.qemu, goldfish) 노출 피하기.
  Waydroid는 qemu 속성은 없지만 `ro.hardware`=`ranchu`/`goldfish` 등 컨테이너 기본값이 신호가 될 수 있어
  점검 권장(미검증 — 실측 필요).

## 5. 미검증/남은 점

- SM-X710에서 카카오톡 "다른 기기와 함께 사용" 옵션이 실제로 뜨는지 **직접 후기 미확보** → 실기동 검증 필요.
- bare `ro.build.fingerprint` 라인은 덤프에 없어 조립값 사용(구성요소는 전부 검증).
- 카카오톡의 Play Integrity 강제 여부, Waydroid 기본 속성 중 에뮬 신호 범위는 미검증 — 로그인 실패 시 이 두
  축을 먼저 의심.
- 대체 모델 덤프는 tadiphone API로 직접 확인 결과 **gts8wifi/gts8/gts8uwifi/gts8pwifi 모두 HTTP 404(부재)**.
  SM-X700 등의 전체 build.prop은 미확보 — 필요 시 sammobile/samfw에서 해당 PDA로 재수집.
- X710 확보 펌웨어는 Android 14 / 보안패치 2024-12-01로 조사 시점(2026-10) 기준 약 2년 구형. 스푸핑 용도로는
  무방하나(모델 식별이 목적), 최신 패치 지문이 필요하면 samfw에서 최신 X710XXU* PDA로 갱신 가능.

## 6. 출처 정리

- AndroidDumps gts9wifi 덤프(실측 build.prop): https://dumps.tadiphone.dev/dumps/samsung/gts9wifi
- AndroidDumps gts9(SM-X716B, 5G 변종 참고): https://dumps.tadiphone.dev/dumps/samsung/gts9
- GSMArena Galaxy Tab S9 사양(해상도): https://www.gsmarena.com/samsung_galaxy_tab_s9-12439.php
- 디시 태블릿갤 App Cloner로 SM-X700 스푸핑: https://gall.dcinside.com/board/view/?id=tabletpc&no=971423
- 디시 리눅스갤 Anbox build.prop 갤탭 인식: https://gall.dcinside.com/mgallery/board/view/?id=linux&no=1135
- 하모니카 카카오톡 멀티로그인/ build.prop: https://hamonikr.org/index.php?mid=Free_Board&document_srl=113075
- 클리앙 폰+태블릿 멀티디바이스/build.prop: https://www.clien.net/service/board/cm_andro/17938896
- Waydroid Prop Options 공식문서: https://docs.waydro.id/usage/waydroid-prop-options
- Quackdoc/waydroid-scripts spoof-device.sh: https://github.com/Quackdoc/waydroid-scripts/blob/main/spoof-device.sh
- 본 저장소 선행조사(dex allowlist/서버판정): docs/initiatives/2026-10-16-prior-art.md, 2026-10-16-design.md §3.1

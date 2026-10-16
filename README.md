# kakao-cli

공식 Android 카카오톡을 **Waydroid**에서 구동하고 **adb UI 자동화**로 제어하는 Linux CLI.

- 설계: `docs/initiatives/2026-10-16-design.md`
- 환경 구축(Proxmox VM/CT + Waydroid + 태블릿 스푸핑): `docs/setup-waydroid.md`
- 수집/분석 원장: `docs/initiatives/2026-10-16-artifact-collection.md`
- 선행 사례 분석: `docs/initiatives/2026-10-16-prior-art.md`

> 서버 프로토콜(LOCO) 재구현도, 로컬 DB 복호화도 하지 않는다(계정 차단/봉인 이슈). 공식 앱을 UI 자동화로 조작한다.

## 개발
```bash
uv sync
uv run kakao doctor --connect <vm-ip>:5555   # adb·디바이스·앱버전 점검
uv run pytest
```

## 상태
`doctor`만 라이브 adb에 연결됨. `send`/`read`/`watch`/`rooms`/`memo`는 Waydroid 실측으로
자동화 계약(리소스ID·intent) 확정 후 구현(design.md §5).

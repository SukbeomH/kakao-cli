# Android Console Input (MIT)

별도 작성한 `InputMethodService`다. ADBKeyBoard 소스/바이너리를 수정하거나 포함하지 않는다.
`setComposingText`로 조합 영역을 교체하고 `commitText`로 한 번 확정한다.
기존 CLI/TUI와 호환되는 `ADB_INPUT_TEXT`, `ADB_CLEAR_TEXT`도 받는다.
수신자는 Android shell/root의 `android.permission.DUMP` 권한을 요구한다.
네트워크·클립보드·저장소 접근 및 텍스트 로깅을 하지 않는다.

패키지는 `org.androidconsole.ime`, 최소 Android 8(API 26)이다.
현재 선택된 입력기와 설치된 APK 해시를 Python 전송기가 검사한다.
새 입력 칸에서는 이전 세션을 폐기해 늦은 조합 요청이 다른 입력 칸을 수정하지 않도록 한다.
한 조합 세션은 한 브라우저가 소유하며 다른 브라우저의 진행 중 조합을 빼앗지 않는다.

## 빌드

JDK와 Android SDK platform 36, build-tools 36.1.0을 설치한 뒤:

```sh
python build.py --sdk /path/to/android-sdk
# 배포 APK를 바꾸지 않고 서명·해시·현재 소스와 DEX 일치 확인
python build.py --sdk /path/to/android-sdk --verify
```

`android-console-ime.apk`와 대응 SHA-256 파일을 갱신한다. 빌드 후 apksigner 검증을 실행한다.
서명 키는 `~/.cache/android-console/build/ime.jks`에 생성하며 저장소·번들에 포함하지 않는다.
기존 설치에 업데이트하려면 같은 서명 키를 유지해야 한다. 키를 잃은 개발 빌드는 기존 입력기로
전환 후 이 보조 앱만 제거하고 다시 설치한다. 카카오톡이나 Android 데이터를 지울 필요는 없다.
APK는 자체 소프트웨어의 배포 산출물로 포함하며 제3자 APK는 기존처럼 설치 시 내려받는다.

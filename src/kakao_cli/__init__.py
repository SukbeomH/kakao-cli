"""kakao-cli: drive the official KakaoTalk Android app (Waydroid) over adb."""
__version__ = "0.1.0"

PACKAGE = "com.kakao.talk"
LAUNCHER_ACTIVITY = "com.kakao.talk.activity.SplashActivity"
CHATROOM_ACTIVITY = "com.kakao.talk.activity.chatroom.ChatRoomHolderActivity"
MEMO_ACTIVITY = "com.kakao.talk.activity.MemoChatConnectActivity"  # self-chat, safe test target

# UI resource-ids observed in decompiled 26.8.3 (names are NOT obfuscated).
# Live truth is `uiautomator dump`; these are starting candidates (see docs/initiatives/2026-10-16-design.md §5).
RID = {
    "room_list": f"{PACKAGE}:id/chatroom_list",
    "compose": f"{PACKAGE}:id/compose_view",
    "send_button": f"{PACKAGE}:id/sendButton",
    "message": f"{PACKAGE}:id/message",
}

"""kakao-cli: drive the official KakaoTalk Android app (Waydroid) over adb."""
__version__ = "0.1.2"

PACKAGE = "com.kakao.talk"
LAUNCHER_ACTIVITY = "com.kakao.talk.activity.SplashActivity"
CHATROOM_ACTIVITY = "com.kakao.talk.activity.chatroom.ChatRoomHolderActivity"
MEMO_ACTIVITY = "com.kakao.talk.activity.MemoChatConnectActivity"  # self-chat, safe test target

# UI resource-ids CONFIRMED LIVE on redroid (KakaoTalk 26.8.3, logged-in tablet).
# Names are NOT obfuscated. Live `uiautomator dump` is authoritative.
RID = {
    # login (AuthenticatorActivity)
    "login_account": f"{PACKAGE}:id/account_key",      # parent of edit_text
    "login_password": f"{PACKAGE}:id/password",         # parent of edit_text
    "login_edit": f"{PACKAGE}:id/edit_text",
    "login_subdevice_chk": f"{PACKAGE}:id/chk_subdevice",
    "login_button": f"{PACKAGE}:id/login_kakao_account",
    "login_qr": f"{PACKAGE}:id/signup_kakao_account_with_qr",
    # chat list (MainActivity > Chats tab)
    "room_list": f"{PACKAGE}:id/recycler_view",
    "room_name": f"{PACKAGE}:id/name",
    "room_last_message": f"{PACKAGE}:id/message",
    "room_time": f"{PACKAGE}:id/time",
    "room_members_count": f"{PACKAGE}:id/members_count",
    # chat room (ChatRoomHolderActivity)
    "input": f"{PACKAGE}:id/message_edit_text",
    "send": f"{PACKAGE}:id/send_layout",
    "media_send": f"{PACKAGE}:id/media_send_layout",
    "chat_log": f"{PACKAGE}:id/chat_log_recycler_list",
}
# bottom nav tabs are matched by content-desc: "Friends tab", "Chats tab", "More tab".
MEMO_ROUTE = "Friends tab > my avatar (toolbar) > My Chatroom"  # self-chat, safe test target

"""KakaoTalk login detection plus shared screen helpers."""
from android_console.screen import ScreenBuffer, png_size, view_to_device

# login / onboarding activities & resource-ids (from the documented login contract)
_LOGIN_ACT_HINTS = ("Authenticator", "AccountManage", "SignupQr", "LoginActivity")
_LOGIN_RID_HINTS = ("signup_kakao_account_with_qr", "chk_subdevice",
                    "login_kakao_account", "account_key")


def is_login_screen(activity: str, xml: str = "") -> bool:
    """True when the app is showing a login / sub-device onboarding screen."""
    a = activity or ""
    if any(h in a for h in _LOGIN_ACT_HINTS):
        return True
    x = xml or ""
    return any(rid in x for rid in _LOGIN_RID_HINTS)

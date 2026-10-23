"""KakaoTalk errors share the Android console's transport error contract."""
from android_console.errors import ConsoleError as KakaoError
from android_console.errors import AdbUnavailable, DeviceNotFound, AppNotInstalled, ConsoleBusy


class SendUncertain(KakaoError):
    """Submission happened but readback could not confirm it. Never auto-retry."""
    code = "send_uncertain"
    exit_code = 6

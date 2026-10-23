"""KakaoTalk branding and display policy for the shared Android console."""
from android_console.webui import *
from android_console import webui as _shared
from android_console.profile import ConsoleProfile
from . import PACKAGE

PROFILE = ConsoleProfile(name="kakao-cli", storage_prefix="kakao", default_mode="virtual",
                         target_app=PACKAGE, navigation=False, system_decorations=False,
                         cache_dir="~/.cache/kakao-cli",
                         help_suffix=" · 채팅은 CLI/TUI(kakao tui)에서", upload_header="X-Kakao-Op")
PAGE = _shared.render_page(PROFILE)


def make_handler(*args, **kwargs):
    kwargs.setdefault("profile", PROFILE)
    return _shared.make_handler(*args, **kwargs)


def serve(*args, **kwargs):
    kwargs.setdefault("profile", PROFILE)
    return _shared.serve(*args, **kwargs)


def run(*args, **kwargs):
    kwargs.setdefault("profile", PROFILE)
    if len(args) < 5:
        kwargs.setdefault("port", 8800)
    return _shared.run(*args, **kwargs)

"""KakaoTalk policy over the shared Android video hub."""
from android_console.video import *
from android_console import video as _shared
from . import PACKAGE

APP = PACKAGE

class VideoHub(_shared.VideoHub):
    def __init__(self, bridge, dev, mode="virtual", max_fps=60, bit_rate=4_000_000,
                 max_size=1280, dpi_mode="auto", vd_max_size=1280, **kwargs):
        kwargs.setdefault("target_app", PACKAGE)
        kwargs.setdefault("system_decorations", False)
        kwargs.setdefault("grace_s", GRACE_S)
        super().__init__(bridge, dev, mode=mode, max_fps=max_fps, bit_rate=bit_rate,
                         max_size=max_size, dpi_mode=dpi_mode, vd_max_size=vd_max_size, **kwargs)

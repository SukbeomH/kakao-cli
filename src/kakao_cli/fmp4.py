"""Compatibility alias; implementation lives in android_console."""
import sys
from android_console import fmp4 as _shared
sys.modules[__name__] = _shared

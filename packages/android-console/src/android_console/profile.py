"""Application policy injected by console consumers."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ConsoleProfile:
    name: str = "android-console"
    storage_prefix: str = "android-console"
    default_mode: str = "mirror"
    target_app: str | None = None
    navigation: bool = True
    system_decorations: bool = True
    cache_dir: str = "~/.cache/android-console"
    help_suffix: str = ""
    upload_header: str = "X-Android-Console-Op"


DEFAULT_PROFILE = ConsoleProfile()

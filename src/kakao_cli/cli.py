"""kakao-cli entry point. Commands are thin; safety/automation live in dedicated modules.

Only `doctor` is wired to live adb so far. send/read/watch are declared but gated
until the automation engine is validated against a real Waydroid device (see
docs/initiatives/2026-10-16-design.md).
"""
from __future__ import annotations
import argparse
import sys

from . import __version__, PACKAGE
from .errors import KakaoError
from .ioutil import emit
from . import adb


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    p.add_argument("--serial", help="adb device serial")
    p.add_argument("--connect", dest="addr", help="adb connect target, e.g. 10.0.0.5:5555")


def cmd_doctor(args) -> dict:
    dev = adb.connect(args.serial, args.addr)
    version = adb.require_app(dev)
    return {
        "ok": True,
        "serial": dev.serial,
        "package": PACKAGE,
        "app_version": version,
        "current_activity": dev.current_activity(),
    }


def cmd_notimpl(args) -> dict:
    raise KakaoError(
        f"'{args._name}' not implemented yet: needs live Waydroid validation of the "
        "automation contract (see docs/initiatives/2026-10-16-design.md)"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kakao", description="Drive KakaoTalk (Waydroid) over adb")
    ap.add_argument("--version", action="version", version=f"kakao-cli {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="check adb, device, app version")
    _common(d)
    d.set_defaults(func=cmd_doctor)

    for name, help_ in [
        ("rooms", "list chat rooms"),
        ("send", "send a message (proposal/approval by default)"),
        ("read", "read recent messages"),
        ("watch", "stream incoming messages (NDJSON)"),
        ("memo", "send to self-chat (safe test)"),
    ]:
        sp = sub.add_parser(name, help=help_)
        _common(sp)
        sp.set_defaults(func=cmd_notimpl, _name=name)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_json = getattr(args, "json", False)
    try:
        result = args.func(args)
        emit(result, as_json=as_json)
        return 0
    except KakaoError as e:
        emit(e.to_dict(), as_json=as_json)
        return e.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

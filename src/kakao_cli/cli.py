"""kakao-cli entry point. Thin commands over the driver; structured JSON output."""
from __future__ import annotations
import argparse
import sys

from . import __version__, PACKAGE
from .errors import KakaoError
from .ioutil import emit
from . import adb
from .driver import Driver


def _dev(args):
    return adb.connect(args.serial, args.addr, adb=args.adb)


def cmd_doctor(args) -> dict:
    dev = _dev(args)
    version = adb.require_app(dev)
    return {"ok": True, "serial": dev.serial, "package": PACKAGE,
            "app_version": version, "current_activity": dev.current_activity()}


def cmd_rooms(args) -> dict:
    d = Driver(_dev(args))
    rooms = d.list_rooms()
    return {"ok": True, "count": len(rooms), "rooms": rooms}


def cmd_read(args) -> dict:
    d = Driver(_dev(args))
    msgs = d.read_room(args.room, limit=args.limit)
    return {"ok": True, "room": args.room, "count": len(msgs), "messages": msgs}


def cmd_send(args) -> dict:
    d = Driver(_dev(args))
    if not args.yes:
        raise KakaoError("refusing to send without --yes (proposal/approval)",
                         room=args.room, text=args.text)
    d.open_room(args.room)
    result = d.send_in_room(args.text)
    return {"ok": True, "room": args.room, "text": args.text, "result": result}


def cmd_memo(args) -> dict:
    d = Driver(_dev(args))
    d.open_self_chat()
    result = d.send_in_room(args.text)
    return {"ok": True, "target": "self-chat", "text": args.text, "result": result}


def _common(p):
    p.add_argument("--json", action="store_true")
    p.add_argument("--serial")
    p.add_argument("--connect", dest="addr")
    p.add_argument("--adb", default="adb", help="adb binary path")


def build_parser():
    ap = argparse.ArgumentParser(prog="kakao", description="Drive KakaoTalk (redroid) over adb")
    ap.add_argument("--version", action="version", version=f"kakao-cli {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    for name, fn, extra in [
        ("doctor", cmd_doctor, None),
        ("rooms", cmd_rooms, None),
        ("read", cmd_read, "read"),
        ("send", cmd_send, "send"),
        ("memo", cmd_memo, "memo"),
    ]:
        sp = sub.add_parser(name)
        _common(sp)
        if extra == "read":
            sp.add_argument("room", nargs="?", default=None)
            sp.add_argument("--limit", type=int, default=20)
        elif extra == "send":
            sp.add_argument("room")
            sp.add_argument("text")
            sp.add_argument("--yes", action="store_true", help="actually send")
        elif extra == "memo":
            sp.add_argument("text")
        sp.set_defaults(func=fn)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    as_json = getattr(args, "json", False)
    try:
        emit(args.func(args), as_json=as_json)
        return 0
    except KakaoError as e:
        emit(e.to_dict(), as_json=as_json)
        return e.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

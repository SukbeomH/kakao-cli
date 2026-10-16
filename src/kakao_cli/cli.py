"""kakao-cli entry point. Thin commands over the driver; structured JSON output."""
from __future__ import annotations
import argparse
import sys

from . import __version__, PACKAGE
from .errors import KakaoError
from .ioutil import emit
from . import adb
from .driver import Driver
from . import notify
from . import media


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
    if getattr(args, "home", False):
        d.dev.shell("input keyevent KEYCODE_HOME")
    return {"ok": True, "room": args.room, "text": args.text, "result": result}


def cmd_memo(args) -> dict:
    d = Driver(_dev(args))
    d.open_self_chat()
    result = d.send_in_room(args.text)
    if getattr(args, "home", False):
        d.dev.shell("input keyevent KEYCODE_HOME")
    return {"ok": True, "target": "self-chat", "text": args.text, "result": result}




def cmd_watch(args) -> int:
    import json, sys
    dev = _dev(args)
    for ev in notify.watch(dev, interval=args.interval):
        json.dump(ev, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n"); sys.stdout.flush()
    return 0



def cmd_send_file(args) -> dict:
    d = Driver(_dev(args))
    if not args.yes:
        raise KakaoError("refusing to send without --yes", target=args.target, file=args.file)
    result = d.send_file(args.target, args.file)
    if getattr(args, "home", False):
        d.dev.shell("input keyevent KEYCODE_HOME")
    return {"ok": True, "target": args.target, "file": args.file, "result": result}



def cmd_pull_media(args) -> dict:
    import json, os
    dev = _dev(args)
    if getattr(dev, "serial", None):
        import subprocess
        subprocess.run([dev.adb, "-s", dev.serial, "root"], capture_output=True, timeout=20)
    state = os.path.join(args.dest, ".seen.json")
    seen = set()
    if args.new_only and os.path.exists(state):
        try:
            seen = set(json.load(open(state)))
        except Exception:
            seen = set()
    pulled = media.pull_new(dev, args.dest, seen)
    if args.new_only:
        os.makedirs(args.dest, exist_ok=True)
        json.dump(sorted(seen), open(state, "w"))
    return {"ok": True, "dest": args.dest, "new_only": args.new_only,
            "count": len(pulled), "files": pulled}



def cmd_track(args) -> int:
    import json, sys
    d = Driver(_dev(args))
    for ev in d.track(interval=args.interval, window=args.window):
        json.dump(ev, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n"); sys.stdout.flush()
    return 0



def cmd_poll(args) -> int:
    import json, sys
    d = Driver(_dev(args))
    for ev in d.poll_room(args.room, interval=args.interval, limit=args.limit):
        json.dump(ev, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n"); sys.stdout.flush()
    return 0


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
        ("watch", cmd_watch, "watch"),
        ("send-file", cmd_send_file, "send-file"),
        ("pull-media", cmd_pull_media, "pull-media"),
        ("track", cmd_track, "track"),
        ("poll", cmd_poll, "poll"),
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
            sp.add_argument("--home", action="store_true", help="background app after send (receive-ready)")
        elif extra == "memo":
            sp.add_argument("text")
            sp.add_argument("--home", action="store_true")
        elif extra == "watch":
            sp.add_argument("--interval", type=float, default=3.0)
        elif extra == "send-file":
            sp.add_argument("target", help="room name or 'me'")
            sp.add_argument("file")
            sp.add_argument("--yes", action="store_true")
            sp.add_argument("--home", action="store_true")
        elif extra == "pull-media":
            sp.add_argument("dest")
            sp.add_argument("--new-only", action="store_true", help="only pull media not seen before (state in <dest>/.seen.json)")
        elif extra == "track":
            sp.add_argument("--interval", type=float, default=5.0)
            sp.add_argument("--window", type=int, default=10)
        elif extra == "poll":
            sp.add_argument("room", help="room name or 'me'")
            sp.add_argument("--interval", type=float, default=3.0)
            sp.add_argument("--limit", type=int, default=10)
        sp.set_defaults(func=fn)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    as_json = getattr(args, "json", False)
    try:
        result = args.func(args)
        if isinstance(result, int):
            return result
        emit(result, as_json=as_json)
        return 0
    except KakaoError as e:
        emit(e.to_dict(), as_json=as_json)
        return e.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

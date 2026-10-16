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



def cmd_tui(args) -> int:
    from . import tui
    tui.run(serial=args.serial, addr=args.addr, adb=args.adb)
    return 0


def cmd_webui(args) -> int:
    from . import webui
    webui.run(serial=args.serial, addr=args.addr, adb=args.adb,
              host=args.host, port=args.port)
    return 0


_EPILOG = """\
examples:
  kakao doctor --connect 10.0.0.30:5555     # check the device/app
  kakao rooms --json                        # list chat rooms (name/unread/last msg)
  kakao send "Mom" "집에 가는 중" --yes       # send text (requires --yes)
  kakao memo "메모" --home                    # self-chat, then background the app
  kakao send-file "Team" ./clip.mp4 --yes    # send image/video/document
  kakao poll me                              # stream new messages from one room (NDJSON)
  kakao pull-media ./inbox --new-only        # pull received media incrementally
  kakao tui                                  # interactive terminal UI
  kakao webui --port 8800                    # same UI in a browser (localhost)

Receive model: `poll <room>` opens one room and streams newly-arrived messages
(text + [Photo]/[Video]); robust, no notification/background dependency. `watch`
is an alternative that reads OS notifications (requires the app backgrounded).
"""

_DESC = {
    "doctor": "Check adb connectivity, the installed app version and current screen.",
    "rooms": "List chat rooms with last message, time, member and unread counts.",
    "read": "Read recent messages from a room (or the currently open room).",
    "send": "Send a text message to a room (requires --yes).",
    "memo": "Send a text message to your own chat (나와의 채팅) — safe for testing.",
    "watch": "Stream incoming messages via OS notifications (app must be backgrounded).",
    "send-file": "Send an image, video or document (pdf/zip/doc/...) to a room.",
    "pull-media": "Pull media blobs (sent/received) from the app to a local directory.",
    "track": "Poll the whole chat list and emit each room's newest message when it changes.",
    "poll": "Open ONE room and stream its newly-arrived messages (recommended receive).",
    "tui": "Interactive terminal UI: browse rooms, read live, and send.",
    "webui": "Serve the TUI's UI in a browser (stdlib HTTP, localhost by default).",
}


def _common(p):
    g = p.add_argument_group("connection / output")
    g.add_argument("--json", action="store_true", help="machine-readable JSON output")
    g.add_argument("--serial", metavar="SERIAL", help="adb device serial (default: first device)")
    g.add_argument("--connect", dest="addr", metavar="HOST:PORT",
                   help="adb connect target before running, e.g. 10.0.0.30:5555")
    g.add_argument("--adb", default="adb", metavar="PATH", help="adb binary path (default: adb)")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="kakao",
        description="Drive the official KakaoTalk Android app (in redroid/Waydroid) over adb.",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"kakao-cli {__version__}")
    sub = ap.add_subparsers(dest="command", required=True, metavar="<command>")

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
        ("tui", cmd_tui, "tui"),
        ("webui", cmd_webui, "webui"),
    ]:
        sp = sub.add_parser(name, help=_DESC[name], description=_DESC[name])
        _common(sp)
        if extra == "read":
            sp.add_argument("room", nargs="?", default=None, help="room name or 'me' (default: current room)")
            sp.add_argument("--limit", type=int, default=20, metavar="N", help="max messages (default: 20)")
        elif extra == "send":
            sp.add_argument("room", help="target room name (exact)")
            sp.add_argument("text", help="message text (Unicode OK via ADBKeyBoard)")
            sp.add_argument("--yes", action="store_true", help="confirm the send (required)")
            sp.add_argument("--home", action="store_true", help="background the app after sending")
        elif extra == "memo":
            sp.add_argument("text", help="message text")
            sp.add_argument("--home", action="store_true", help="background the app after sending")
        elif extra == "watch":
            sp.add_argument("--interval", type=float, default=3.0, metavar="SEC", help="poll interval (default: 3)")
        elif extra == "send-file":
            sp.add_argument("target", help="room name or 'me'")
            sp.add_argument("file", help="local path; image/video or document (pdf/zip/doc/...)")
            sp.add_argument("--yes", action="store_true", help="confirm the send (required)")
            sp.add_argument("--home", action="store_true", help="background the app after sending")
        elif extra == "pull-media":
            sp.add_argument("dest", help="local directory to write pulled media into")
            sp.add_argument("--new-only", action="store_true",
                            help="only pull blobs not seen before (state: <dest>/.seen.json)")
        elif extra == "track":
            sp.add_argument("--interval", type=float, default=5.0, metavar="SEC", help="poll interval (default: 5)")
            sp.add_argument("--window", type=int, default=10, metavar="N", help="rolling recent window (default: 10)")
        elif extra == "poll":
            sp.add_argument("room", help="room name or 'me'")
            sp.add_argument("--interval", type=float, default=3.0, metavar="SEC", help="poll interval (default: 3)")
            sp.add_argument("--limit", type=int, default=10, metavar="N", help="messages to scan per poll (default: 10)")
        elif extra == "webui":
            sp.add_argument("--host", default="127.0.0.1", metavar="HOST",
                            help="bind address (default: 127.0.0.1; 0.0.0.0 exposes it unauthenticated)")
            sp.add_argument("--port", type=int, default=8800, metavar="PORT", help="TCP port (default: 8800)")
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

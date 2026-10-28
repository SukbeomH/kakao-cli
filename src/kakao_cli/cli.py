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


def _ui_dev(args):
    """Device for commands that drive the app UI: refuse while the web console
    has KakaoTalk on its virtual display (taps would hit the wrong display)."""
    from . import consolelock
    dev = _dev(args)
    consolelock.check(dev)
    return dev


def cmd_doctor(args) -> dict:
    from . import consolelock
    dev = _dev(args)
    version = adb.require_app(dev)
    busy, info = consolelock.parse(dev.shell("cat %s 2>/dev/null; echo ==; date +%%s" % consolelock.LOCK))
    return {"ok": True, "serial": dev.serial, "package": PACKAGE,
            "app_version": version, "current_activity": dev.current_activity(),
            "web_console_virtual_display": info if busy else None}


def _receiver(args):
    from .receiver import Receiver
    return Receiver(_dev(args), getattr(args, "source", "db"))


def cmd_rooms(args) -> dict:
    receiver = _receiver(args)
    rooms = receiver.list_rooms()
    return {"ok": True, "count": len(rooms), "rooms": rooms, "source": receiver.source}


def cmd_read(args) -> dict:
    receiver = _receiver(args)
    detailed = receiver.read_detailed(args.room, limit=args.limit)
    return {"ok": True, "room": args.room, "count": len(detailed),
            "messages": [m["text"] for m in detailed],
            "message_details": detailed, "source": receiver.source}


def cmd_send(args) -> dict:
    d = Driver(_ui_dev(args))
    if not args.yes:
        raise KakaoError("refusing to send without --yes (proposal/approval)",
                         room=args.room, text=args.text)
    d.open_room(args.room)
    result = d.send_in_room(args.text)
    if getattr(args, "home", False):
        d.dev.shell("input keyevent KEYCODE_HOME")
    return {"ok": True, "room": args.room, "text": args.text, "result": result}


def cmd_memo(args) -> dict:
    d = Driver(_ui_dev(args))
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
    d = Driver(_ui_dev(args))
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
    d = Driver(_ui_dev(args))
    for ev in d.track(interval=args.interval, window=args.window):
        json.dump(ev, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n"); sys.stdout.flush()
    return 0



def cmd_poll(args) -> int:
    import json, sys
    d = _receiver(args)
    for ev in d.poll_room(args.room, interval=args.interval, limit=args.limit):
        json.dump(ev, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n"); sys.stdout.flush()
    return 0



def cmd_tui(args) -> int:
    from . import tui
    tui.run(serial=args.serial, addr=args.addr, adb=args.adb, receiver=_receiver(args), vpn_url=args.vpn_url)
    return 0


def cmd_webui(args) -> int:
    from . import webui
    webui.run(serial=args.serial, addr=args.addr, adb=args.adb,
              host=args.host, port=args.port, allow_screen=args.allow_screen,
              fs_root=args.fs_root, max_upload=args.max_upload,
              allow_hosts=args.allow_host or (), scrcpy_server=args.scrcpy_server,
              use_scrcpy=not args.no_scrcpy, video_mode=args.video_mode,
              video_max_fps=args.video_max_fps, video_bit_rate=args.video_bit_rate,
              video_max_size=args.video_max_size, vd_dpi=args.vd_dpi, vd_max_size=args.vd_max_size,
              network_socket=args.network_socket)
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
  kakao webui --port 8800                    # device console in a browser (localhost)

Receive model: `poll <room>` defaults to read-only DB incremental reception. UI fallback streams messages
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
    "webui": "Device console in a browser: live screen + keyboard + file manager (chat: use tui).",
}


def _size(v: str) -> int:
    """'2G' / '500M' / '1048576' → bytes."""
    v = str(v).strip().upper()
    mult = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}.get(v[-1:], 1)
    num = v[:-1] if v[-1:] in "KMGT" else v
    try:
        return int(float(num) * mult)
    except ValueError:
        raise argparse.ArgumentTypeError("bad size: " + v)


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
    from android_console.vpn import add_parser
    add_parser(sub, 'http://127.0.0.1:8800')

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
        if name in ("rooms", "read", "poll"):
            sp.add_argument("--source", choices=("db", "ui"), default="db",
                            help="수신 경로 (기본 db, 실패하면 경고 후 ui)")
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
        elif extra == "tui":
            sp.add_argument('--vpn-url', default='http://127.0.0.1:8800', help='VM console URL for the V-key VPN panel')
        elif extra == "webui":
            sp.add_argument('--network-socket', help='Opt-in Linux VM VPN management Unix socket')
            sp.add_argument("--host", default="127.0.0.1", metavar="HOST",
                            help="bind address (default: 127.0.0.1; 0.0.0.0 exposes it unauthenticated)")
            sp.add_argument("--port", type=int, default=8800, metavar="PORT", help="TCP port (default: 8800)")
            sp.add_argument("--allow-screen", action="store_true",
                            help="enable the live screen view/control on a non-local --host "
                                 "(full-account remote control; off by default when not on 127.0.0.1)")
            sp.add_argument("--max-upload", type=_size, default="2G", metavar="SIZE",
                            help="largest browser upload into the console (default: 2G)")
            sp.add_argument("--allow-host", action="append", metavar="NAME",
                            help="extra Host name accepted by console APIs besides IP "
                                 "literals/localhost (e.g. a tailscale serve name); repeatable")
            sp.add_argument("--scrcpy-server", metavar="PATH",
                            help="offline scrcpy-server v5.0.1 file (sha256-verified); default: "
                                 "download once into ~/.cache/kakao-cli/")
            sp.add_argument("--no-scrcpy", action="store_true",
                            help="don't start scrcpy-server (no clipboard/video; screencap + adb input)")
            sp.add_argument("--video-mode", choices=("virtual", "mirror"), default="virtual",
                            help="console video: a scrcpy virtual display sized to the browser "
                                 "(flex, default) or a mirror of the device screen")
            sp.add_argument("--video-max-fps", type=int, default=60, metavar="N",
                            help="H.264 frame-rate cap (default 60; Android refresh rate is separate)")
            sp.add_argument("--video-bit-rate", type=_size, default="4M", metavar="BPS",
                            help="H.264 bit rate (default 4M)")
            sp.add_argument("--video-max-size", type=int, default=1280, metavar="PX",
                            help="mirror mode: longest side (default 1280)")
            sp.add_argument("--vd-dpi", default="auto", metavar="auto|device|N",
                            help="virtual display dpi: auto = 160×devicePixelRatio (1 dp ≈ 1 CSS px), "
                                 "device = proportional to the device's 340 dpi, or a number")
            sp.add_argument("--vd-max-size", type=int, choices=(960, 1280, 1440, 1920), default=1280,
                            help="virtual display longest side / initial quality (default 1280)")
            sp.add_argument("--fs-root", default="~", metavar="PATH",
                            help="local file-manager root in the console (default: ~); "
                                 "the pane cannot leave it")
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

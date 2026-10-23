"""Standalone entry point. No application-specific imports."""
from __future__ import annotations
import argparse
from dataclasses import replace
import re
import sys

from . import __version__
from .errors import ConsoleError
from .profile import DEFAULT_PROFILE


def size(value):
    match = re.fullmatch(r"([1-9][0-9]*)([KMG]?)", value.upper())
    if not match:
        raise argparse.ArgumentTypeError("use a positive integer, optionally suffixed K/M/G")
    return int(match[1]) * 1024 ** (" KMG".index(match[2]) if match[2] else 0)


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def build_parser():
    parser = argparse.ArgumentParser(prog="android-console")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="open an Android device in the browser")
    serve.add_argument("--serial")
    serve.add_argument("--connect", dest="addr")
    serve.add_argument("--adb", default="adb")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=positive, default=8801)
    serve.add_argument("--allow-screen", action="store_true")
    serve.add_argument("--fs-root", default="~")
    serve.add_argument("--max-upload", type=size, default=2 << 30)
    serve.add_argument("--allow-host", action="append", default=[], dest="allow_hosts")
    serve.add_argument("--scrcpy-server")
    serve.add_argument("--no-scrcpy", action="store_false", dest="use_scrcpy")
    serve.add_argument("--video-mode", choices=("mirror", "virtual"), default="mirror")
    serve.add_argument("--app", help="package to launch on an optional virtual display")
    serve.add_argument("--video-max-fps", type=positive, default=60)
    serve.add_argument("--video-bit-rate", type=size, default=4_000_000)
    serve.add_argument("--video-max-size", type=positive, default=1280)
    serve.add_argument("--vd-dpi", default="auto")
    serve.add_argument("--vd-max-size", type=int, choices=(960, 1280, 1440, 1920), default=1280)
    runtime = commands.add_parser("runtime", help="export an isolated redroid/Lima runtime")
    actions = runtime.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export", help="write a new self-contained runtime folder")
    export.add_argument("directory")
    return parser


def main(argv=None):
    parser = build_parser()
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        if command == "runtime":
            from .runtime import export_runtime
            print(export_runtime(args["directory"]))
            return 0
        app = args.pop("app")
        if app and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", app):
            parser.error("--app must be an Android package name")
        if args["port"] > 65535:
            parser.error("--port must be <= 65535")
        if args["vd_dpi"] not in ("auto", "device"):
            try:
                positive(args["vd_dpi"])
            except (ValueError, argparse.ArgumentTypeError):
                parser.error("--vd-dpi must be auto, device or a positive integer")
        from .webui import run
        run(**args, profile=replace(DEFAULT_PROFILE, target_app=app))
        return 0
    except ConsoleError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return error.exit_code
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

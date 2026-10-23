"""Serialized Android input and PNG capture, independent of any application."""
from __future__ import annotations
import queue
import threading


class Worker(threading.Thread):
    def __init__(self, dev, poll_interval=2.5, screen=None):
        super().__init__(daemon=True)
        self.dev = dev
        self.cmds = queue.Queue()
        self.events = queue.Queue()
        self.poll_interval = poll_interval
        self.screen = screen
        self._closing = False

    def _startup(self):
        self._emit("status", "ready")

    def put(self, *cmd):
        self.cmds.put(cmd)

    def put_wait(self, *cmd, timeout=5.0):
        """Acknowledge completion, so text followed by Enter stays in order."""
        done, errors = threading.Event(), []
        self.cmds.put(("wait", cmd, done, errors))
        if not done.wait(timeout):
            raise TimeoutError("device input timed out")
        if errors:
            raise errors[0]

    def _emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        self._startup()
        while not self._closing:
            # live screen view: capture frames fast and keep controls responsive;
            # pause the periodic chat poll so there's no adb contention.
            if self.screen is not None and self.screen.active():
                self._capture()
                try:
                    cmd = self.cmds.get(timeout=0.25)
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    try:
                        self._handle(cmd)
                    except Exception as e:
                        self._emit("status", f"err: {e}")
                continue
            try:
                cmd = self.cmds.get(timeout=self.poll_interval)
            except queue.Empty:
                cmd = ("poll",)
            try:
                self._handle(cmd)
            except Exception as e:  # keep the UI alive on transient adb errors
                self._emit("status", f"err: {e}")

    def _handle(self, cmd):
        op = cmd[0]
        if op == "wait":
            try:
                self._handle(cmd[1])
            except Exception as e:
                cmd[3].append(e)
            finally:
                cmd[2].set()
        elif op == "consolekey":
            bridge, code = cmd[1:]
            if bridge:
                bridge.key(code)
            else:
                self.dev.keyevent(code)
        elif op == "quit":
            self._closing = True
        elif op == "tap":
            self.dev.tap(cmd[1], cmd[2])
        elif op == "swipe":
            self.dev.swipe(*cmd[1:])
        elif op == "key":
            if isinstance(cmd[1], int):              # console keyboard passthrough
                self.dev.keyevent(cmd[1])
            else:
                code = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME",
                        "recent": "KEYCODE_APP_SWITCH", "enter": "KEYCODE_ENTER"}.get(cmd[1])
                if code:
                    self.dev.keyevent(code)
        elif op == "screentext":
            self.dev.type_text(cmd[1])
    def _capture(self):
        try:
            self.screen.set_frame(self.dev.screencap_bytes())
        except Exception as e:
            self._emit("status", f"screen capture err: {e}")

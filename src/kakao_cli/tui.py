"""Interactive terminal UI (curses, stdlib only).

A single background worker owns the Driver so all device access is serialized:
it polls the open room, and handles send / switch-room commands between polls.
The curses thread only renders and captures input. Korean input is read with
get_wch() and sent to the device through ADBKeyBoard (driver.send_in_room).
"""
from __future__ import annotations
import curses
import locale
import queue
import threading
import time
import unicodedata

from .adb import connect
from .driver import Driver


# ---- display-width helpers (Korean = 2 cells) ----
def _cw(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _width(s: str) -> int:
    return sum(_cw(c) for c in s)


def _truncw(s: str, w: int) -> str:
    out, used = [], 0
    for c in s:
        cw = _cw(c)
        if used + cw > w:
            break
        out.append(c); used += cw
    return "".join(out)


class Worker(threading.Thread):
    """Owns the Driver. Commands in, events out — one device op at a time."""

    def __init__(self, dev, poll_interval=2.5):
        super().__init__(daemon=True)
        self.d = Driver(dev)
        self.cmds: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self.room: str | None = None
        self.poll_interval = poll_interval
        self._stop = False

    def put(self, *cmd):
        self.cmds.put(cmd)

    def _emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        try:
            self._refresh_rooms()
        except Exception as e:
            self._emit("status", f"rooms load failed: {e} (press r to retry)")
        while not self._stop:
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
        if op == "quit":
            self._stop = True
        elif op == "rooms":
            self._refresh_rooms()
        elif op == "open":
            self.room = cmd[1]
            self._emit("status", f"opening {self.room} …")
            self.d._open_target(self.room)
            self._emit("opened", self.room)
            self._emit("status", f"in {self.room}")
            self._poll_messages()
        elif op == "send":
            if self.room and cmd[1].strip():
                self._emit("status", "sending …")
                res = self.d.send_in_room(cmd[1])
                self._emit("status", f"sent ({res})")
                self._poll_messages()
        elif op == "poll":
            if self.room:
                self._poll_messages()

    def _refresh_rooms(self):
        self._emit("status", "loading rooms …")
        rooms = self.d.list_rooms()
        self._emit("rooms", rooms)
        self._emit("status", f"{len(rooms)} rooms")

    def _poll_messages(self):
        msgs = self.d._messages_on_screen(self.d.screen())
        self._emit("messages", (self.room, msgs))


class TUI:
    def __init__(self, worker: Worker):
        self.w = worker
        self.rooms: list[dict] = []
        self.sel = 0
        self.focus = "list"          # 'list' | 'input'
        self.cur_room: str | None = None
        self.messages: list[str] = []
        self.input = ""
        self.status = "starting …"

    # ---- event pump from the worker ----
    def _drain(self):
        changed = False
        try:
            while True:
                kind, data = self.w.events.get_nowait()
                changed = True
                if kind == "rooms":
                    self.rooms = data
                    self.sel = min(self.sel, max(0, len(self.rooms) - 1))
                elif kind == "status":
                    self.status = data
                elif kind == "opened":
                    self.cur_room = data
                    self.focus = "input"
                elif kind == "messages":
                    room, msgs = data
                    if room == self.cur_room:
                        self.messages = msgs
        except queue.Empty:
            pass
        return changed

    # ---- rendering ----
    def _draw(self, scr):
        scr.erase()
        h, wd = scr.getmaxyx()
        lw = max(24, wd // 3)
        # header
        scr.attron(curses.A_REVERSE)
        scr.addstr(0, 0, _truncw(" kakao-cli  —  Tab: focus  ↑↓/Enter: room  Enter: send  q: quit", wd - 1).ljust(wd - 1))
        scr.attroff(curses.A_REVERSE)
        # room list (left)
        for i, r in enumerate(self.rooms[: h - 3]):
            y = 1 + i
            unread = f" ({r['unread']})" if r.get("unread") else ""
            line = _truncw(r.get("name", ""), lw - len(unread) - 2) + unread
            attr = curses.A_BOLD if r.get("unread") else curses.A_NORMAL
            if i == self.sel and self.focus == "list":
                attr |= curses.A_REVERSE
            try:
                scr.addstr(y, 0, line.ljust(lw - 1)[: lw - 1], attr)
            except curses.error:
                pass
        # vertical divider
        for y in range(1, h - 1):
            try:
                scr.addch(y, lw, curses.ACS_VLINE)
            except curses.error:
                pass
        # messages (right)
        title = self.cur_room or "(select a room)"
        try:
            scr.addstr(1, lw + 2, _truncw("— " + title + " —", wd - lw - 3), curses.A_BOLD)
        except curses.error:
            pass
        view = self.messages[-(h - 4):]
        for j, m in enumerate(view):
            try:
                scr.addstr(2 + j, lw + 2, _truncw(m, wd - lw - 3))
            except curses.error:
                pass
        # input line
        prompt = "> " if self.focus == "input" else "  "
        try:
            scr.addstr(h - 2, lw + 2, _truncw(prompt + self.input, wd - lw - 3))
        except curses.error:
            pass
        # status bar
        scr.attron(curses.A_REVERSE)
        try:
            scr.addstr(h - 1, 0, _truncw(" " + self.status, wd - 1).ljust(wd - 1))
        except curses.error:
            pass
        scr.attroff(curses.A_REVERSE)
        scr.noutrefresh()
        curses.doupdate()

    # ---- main loop ----
    def loop(self, scr):
        curses.curs_set(1)
        scr.keypad(True)
        scr.timeout(150)
        self.w.start()
        while True:
            self._drain()
            self._draw(scr)
            try:
                ch = scr.get_wch()
            except curses.error:
                continue          # timeout tick
            except KeyboardInterrupt:
                break
            if isinstance(ch, str):
                if self.focus == "list":
                    if ch == "q":
                        break
                    if ch == "r":
                        self.w.put("rooms")
                    if ch == "\t":
                        self.focus = "input"
                    elif ch in ("\n", "\r") and self.rooms:
                        self.cur_room = None
                        self.messages = []
                        self.w.put("open", self.rooms[self.sel]["name"])
                else:  # input focus
                    if ch == "\t":
                        self.focus = "list"
                    elif ch in ("\n", "\r"):
                        if self.input.strip():
                            self.w.put("send", self.input)
                            self.input = ""
                    elif ch in ("\x7f", "\b"):
                        self.input = self.input[:-1]
                    elif ch == "\x1b":  # Esc -> back to list
                        self.focus = "list"
                    elif ch.isprintable():
                        self.input += ch
            else:  # special key (int)
                if ch == curses.KEY_UP and self.focus == "list":
                    self.sel = max(0, self.sel - 1)
                elif ch == curses.KEY_DOWN and self.focus == "list":
                    self.sel = min(len(self.rooms) - 1, self.sel + 1)
                elif ch == curses.KEY_BACKSPACE and self.focus == "input":
                    self.input = self.input[:-1]
                elif ch == curses.KEY_RESIZE:
                    pass
        self.w.put("quit")


def run(serial=None, addr=None, adb="adb"):
    locale.setlocale(locale.LC_ALL, "")
    dev = connect(serial, addr, adb=adb)
    worker = Worker(dev)
    tui = TUI(worker)
    curses.wrapper(tui.loop)

#!/usr/bin/env python3
"""Device-side checks for the live screen view (run ON the VM, where adb is).
Verifies: frame bootstrap + size, tap maps to device (toggle Chats/Friends tab
and confirm via dump), frames change on a real UI change, measured fps, and that
capture stops when nobody is watching. Env: BASE, SER. Prints PASS/FAIL lines.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8809")
SER = os.environ.get("SER", "127.0.0.1:5555")
W, H = 1600, 2560
fails = []


def adb(*a):
    return subprocess.run(["adb", "-s", SER, *a], capture_output=True, text=True, timeout=30).stdout


def post(path, body):
    r = urllib.request.Request(BASE + "/api/screen/" + path, data=json.dumps(body).encode(),
                               method="POST", headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=10).read()


def info():
    return json.load(urllib.request.urlopen(BASE + "/api/screen/info", timeout=10))


def frame():
    return urllib.request.urlopen(BASE + "/api/screen/frame", timeout=10).read()


def dump():
    adb("shell", "uiautomator", "dump", "/sdcard/sc.xml")
    return adb("shell", "cat", "/sdcard/sc.xml")


def tabs(xml):
    out = {}
    for m in re.finditer(r'<node[^>]*content-desc="(Chats tab|Friends tab)"[^>]*>', xml):
        seg = m.group(0)
        sel = 'selected="true"' in seg
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', seg)
        c = ((int(b.group(1)) + int(b.group(3))) // 2, (int(b.group(2)) + int(b.group(4))) // 2) if b else None
        out[m.group(1)] = (sel, c)
    return out


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# 1) frame bootstrap + size
f1 = None
for _ in range(25):
    try:
        f1 = frame()
        if f1[:8] == b"\x89PNG\r\n\x1a\n":
            break
    except Exception:
        pass
    time.sleep(2)
import struct
ok = bool(f1 and f1[:8] == b"\x89PNG\r\n\x1a\n")
dims = struct.unpack(">II", f1[16:24]) if ok else None
check("frame bootstrap (PNG %s, %d bytes)" % (dims, len(f1) if f1 else 0), ok)

# 2) tap maps to device: toggle the currently-unselected bottom tab, confirm via dump
adb("shell", "am", "start", "-n", "com.kakao.talk/.activity.main.MainActivity")
time.sleep(2.5)                       # let the viewer go stale so no capture during dump
t0 = tabs(dump())
target = next((n for n, (sel, c) in t0.items() if not sel and c), None)
if target:
    cx, cy = t0[target][1]
    post("tap", {"cx": cx, "cy": cy, "ew": W, "eh": H})   # ew=eh=device → 1:1
    time.sleep(1.5)
    t1 = tabs(dump())
    check("tap toggled %s (selected)" % target, t1.get(target, (False,))[0] is True)
    back = t0.get("Chats tab", (None, None))[1]
    if back:
        post("tap", {"cx": back[0], "cy": back[1], "ew": W, "eh": H}); time.sleep(1)
else:
    check("found a bottom tab to tap", False)

# 3) frames change on a real UI change
h1 = hashlib.sha1(frame()).hexdigest()
fr = t0.get("Friends tab", (None, None))[1]
if fr:
    post("tap", {"cx": fr[0], "cy": fr[1], "ew": W, "eh": H}); time.sleep(1.3)
h2 = hashlib.sha1(frame()).hexdigest()
check("frame changed after UI action", h1 != h2)
back = tabs(dump()).get("Chats tab", (None, None))[1]
if back:
    post("tap", {"cx": back[0], "cy": back[1], "ew": W, "eh": H}); time.sleep(0.5)

# 4) measured fps (unique frames/s while watching) + frame size
stop = False
def pump():
    while not stop:
        try:
            frame()
        except Exception:
            pass
th = threading.Thread(target=pump, daemon=True); th.start()
time.sleep(0.5)
s1 = info()["seq"]; time.sleep(3.0); s2 = info()["seq"]
stop = True
fps = round((s2 - s1) / 3.0, 2)
print("INFO fps=%s frame_bytes~%d" % (fps, len(frame())))

# 5) capture stops when nobody is watching
time.sleep(3.0); a = info()["seq"]; time.sleep(3.0); b = info()["seq"]
check("capture stops when idle (seq %d==%d)" % (a, b), a == b)

print("RESULT " + ("PASS" if not fails else "FAIL: " + ", ".join(fails)))
raise SystemExit(1 if fails else 0)

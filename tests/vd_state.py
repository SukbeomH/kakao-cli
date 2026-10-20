"""Video e2e helper (runs on the VM, stdlib only): which display KakaoTalk is
resumed on, the tab bar as uiautomator sees it (bounds are in that display's
pixels — the virtual display while the console streams), and the console lock.
Prints one JSON line.   SER=127.0.0.1:5555 python3 vd_state.py
"""
import json
import os
import re
import subprocess

SER = os.environ.get("SER", "127.0.0.1:5555")


def sh(cmd):
    return subprocess.run(["adb", "-s", SER, "shell", cmd], capture_output=True,
                          text=True, timeout=30).stdout


def main():
    disp, top = None, {}
    for line in sh("dumpsys activity activities").splitlines():
        m = re.match(r"\s*Display #(\d+)", line)
        if m:
            disp = int(m.group(1))
        m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([\w.]+)/", line)
        if m and disp is not None and disp not in top:
            top[disp] = m.group(1)
    kakao = [d for d, pkg in top.items() if pkg == "com.kakao.talk"]
    sh("uiautomator dump /sdcard/kcv.xml >/dev/null 2>&1")
    xml = sh("cat /sdcard/kcv.xml; rm -f /sdcard/kcv.xml")
    tabs = {}
    for node in re.findall(r"<node [^>]*>", xml):
        d = re.search(r'content-desc="(Friends tab|Chats tab)"', node)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if d and b:
            x1, y1, x2, y2 = map(int, b.groups())
            tabs[d.group(1).split()[0].lower()] = {
                "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                "sel": 'selected="true"' in node}
    lock = sh("cat /data/local/tmp/kakao-cli-console.lock 2>/dev/null").strip()
    print(json.dumps({"kakao_display": kakao[0] if kakao else None,
                      "displays": {str(k): v for k, v in top.items()},
                      "tabs": tabs, "lock": lock}))


if __name__ == "__main__":
    main()

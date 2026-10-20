#!/usr/bin/env python3
"""Device clipboard helper for e2e (run ON the VM; uses kakao_cli.scrcpy).

  hold      read the current device clipboard into memory, print READY, then
            restore it when stdin closes (keeps the user's clipboard off disk/logs)
  hash      print sha256 + length of the current device clipboard (never the text)

Uses its own short-lived scrcpy-server (clipboard_autosync=false so GET works).
"""
import hashlib, os, random, socket, struct, subprocess, sys, time
sys.path.insert(0, os.path.expanduser("~/kakao-cli/src"))
from kakao_cli import scrcpy as S

SER = os.environ.get("SER", "127.0.0.1:5555")
ADB = ["adb", "-s", SER]
JAR = "/data/local/tmp/kakao-cli-cliptool.jar"


def open_ctrl():
    scid = "%08x" % random.randrange(1, 0x7FFFFFFF)
    port = S._free_port()
    subprocess.run(ADB + ["push", S.ensure_server(None), JAR], capture_output=True, check=True)
    subprocess.run(ADB + ["forward", "tcp:%d" % port, "localabstract:scrcpy_" + scid], check=True)
    cmd = ("CLASSPATH=%s app_process / com.genymobile.scrcpy.Server %s scid=%s log_level=warn "
           "tunnel_forward=true video=false audio=false control=true clipboard_autosync=false "
           "cleanup=false send_device_meta=false send_dummy_byte=true" % (JAR, S.VERSION, scid))
    proc = subprocess.Popen(ADB + ["shell", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    for _ in range(100):
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            S._recvn(s, 1)
            return s, proc, port
        except (EOFError, ConnectionResetError):
            s.close(); time.sleep(0.1)
    raise SystemExit("cliptool: server did not start")


def close(s, proc, port):
    s.close(); proc.kill()
    subprocess.run(ADB + ["forward", "--remove", "tcp:%d" % port], capture_output=True)
    subprocess.run(ADB + ["shell", "rm", "-f", JAR], capture_output=True)


def get(s):
    s.sendall(bytes([8, 0]))                          # GET_CLIPBOARD, COPY_KEY_NONE
    p = S.DeviceMsgParser(); s.settimeout(5)
    while True:
        for ev in p.feed(s.recv(65536)):
            if ev[0] == "clipboard":
                return ev[1]


def set_(s, text):
    s.sendall(S.enc_set_clipboard(1, text, False)); s.settimeout(5)
    p = S.DeviceMsgParser()
    while True:
        for ev in p.feed(s.recv(65536)):
            if ev[0] == "ack":
                return


mode = sys.argv[1] if len(sys.argv) > 1 else "hash"
s, proc, port = open_ctrl()
try:
    if mode == "hash":
        t = get(s)
        print("sha256=%s len=%d" % (hashlib.sha256(t.encode()).hexdigest(), len(t)))
    elif mode == "hold":
        orig = get(s)
        close(s, proc, port)
        print("READY sha256=%s" % hashlib.sha256(orig.encode()).hexdigest(), flush=True)
        sys.stdin.read()                                # wait until the harness closes stdin
        s, proc, port = open_ctrl()
        set_(s, orig)
        print("RESTORED len=%d" % len(orig), flush=True)
finally:
    try:
        close(s, proc, port)
    except Exception:
        pass

"""Web UI — the TUI's look and feel in a browser (stdlib only).

Same shape as the curses TUI: a single background :class:`~kakao_cli.worker.Worker`
owns the Driver and serializes device access; the HTTP layer only pushes commands
in and serves a lock-guarded snapshot of state out. No third-party deps, no CDN —
the page (HTML/CSS/JS) is inlined and served from memory.

Endpoints:
  GET  /                      the single-page UI
  GET  /api/state?since=N     current snapshot (204 if seq still == N)
  POST /api/open   {"room"}   open a room
  POST /api/send   {"text"}   send into the open room (400 if none open)
  POST /api/rooms             refresh the room list
"""
from __future__ import annotations
import json
import os
import queue
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

from .adb import connect
from .worker import Worker


class State:
    """Lock-guarded snapshot the HTTP layer serves; fed by the event pump.

    Mirrors the TUI's state transitions exactly: opening a room clears the view
    and leaves ``room`` None until the worker's ``opened`` event, and a
    ``messages`` event for a room other than the current one is dropped — so the
    previous room's log never flashes into the new one.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.rooms: list[dict] = []
        self.room: str | None = None   # confirmed-open room
        self.pending: str | None = None  # room we asked to open
        self.messages: list[dict] = []
        self.status = "starting …"
        self.exports: dict[str, dict] = {}     # export id -> {ready, token?, name?, error?}
        self.downloads: dict[str, dict] = {}   # token -> {path, name, mime}

    def _bump(self):
        self.seq += 1

    def snapshot(self) -> dict:
        with self.lock:
            # serve messages without device-pixel bounds the browser doesn't need
            msgs = [{k: v for k, v in m.items() if k != "bounds"}
                    if isinstance(m, dict) else {"kind": "text", "text": m}
                    for m in self.messages]
            return {"seq": self.seq, "rooms": self.rooms, "room": self.room,
                    "messages": msgs, "status": self.status}

    def export_pending(self, xid: str):
        with self.lock:
            self.exports[xid] = {"ready": False}

    def export_done(self, xid: str, path: str, name: str, mime: str) -> str:
        import uuid
        token = uuid.uuid4().hex
        with self.lock:
            self.downloads[token] = {"path": path, "name": name, "mime": mime}
            self.exports[xid] = {"ready": True, "token": token, "name": name, "mime": mime}
        return token

    def export_failed(self, xid: str, error: str):
        with self.lock:
            self.exports[xid] = {"ready": False, "error": error}

    def export_status(self, xid: str) -> dict | None:
        with self.lock:
            e = self.exports.get(xid)
            return dict(e) if e else None

    def download(self, token: str) -> dict | None:
        with self.lock:
            d = self.downloads.get(token)
            return dict(d) if d else None

    def has_target(self) -> bool:
        """A room is open, or an open is in flight. Commands queue behind that
        open (open is enqueued first), so send/sendfile are safe to accept —
        this avoids a 400 during the brief want_open gap when a row is reclicked."""
        with self.lock:
            return bool(self.room or self.pending)

    def want_open(self, room: str):
        with self.lock:
            self.pending = room
            self.room = None
            self.messages = []
            self._bump()

    def apply(self, kind, data):
        with self.lock:
            # only bump seq on a real change — the worker re-emits identical
            # rooms/status/messages every poll, and bumping each time would make
            # the browser rebuild the DOM ~every 2.5s (flicker, scroll yank,
            # lost input focus). Dedupe here so unchanged polls stay 204.
            if kind == "rooms":
                if data == self.rooms:
                    return
                self.rooms = data
            elif kind == "status":
                if data == self.status:
                    return
                self.status = data
            elif kind == "opened":
                if data == self.room and self.pending is None:
                    return
                self.room = data
                self.pending = None
            elif kind == "messages":
                room, msgs = data
                if room != self.room:
                    return  # stale (previous room) — don't bump
                if msgs == self.messages:
                    return
                self.messages = msgs
            self._bump()


def _pump(worker: Worker, state: State):
    while True:
        try:
            kind, data = worker.events.get(timeout=1.0)
        except queue.Empty:
            continue
        if kind == "export_result":
            state.export_done(data["id"], data["path"], data["name"], data["mime"])
        elif kind == "export_error":
            state.export_failed(data["id"], data.get("error", "export failed"))
        else:
            state.apply(kind, data)


def _safe_name(raw: str) -> str:
    """Browser filename -> a shell/picker-safe basename, keeping the extension.
    `\\w` keeps Hangul; drop path separators and anything that could break the
    unquoted `am broadcast -d file://…` / DocumentsUI search in send_doc."""
    base = os.path.basename(unquote(raw or "")) or "file"
    safe = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "file"
    return safe


def make_handler(worker: Worker, state: State, tmpdir: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kakao-cli-webui"

        def log_message(self, *a):  # silence per-request logging (1s polling)
            pass

        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                since = None
                if "?" in self.path:
                    q = parse_qs(self.path.split("?", 1)[1])
                    if q.get("since"):
                        try:
                            since = int(q["since"][0])
                        except ValueError:
                            since = None
                snap = state.snapshot()
                if since is not None and since == snap["seq"]:
                    self.send_response(204)
                    self.end_headers()
                    return
                self._json(200, snap)
            elif path.startswith("/api/export/"):
                xid = path[len("/api/export/"):]
                st = state.export_status(xid)
                if st is None:
                    return self._json(404, {"error": "unknown export"})
                self._json(200, st)
            elif path.startswith("/api/download/"):
                token = path[len("/api/download/"):]
                d = state.download(token)
                if not d or not os.path.exists(d["path"]):
                    return self._json(404, {"error": "expired"})
                self._serve_file(d["path"], d["name"], d["mime"])
            else:
                self._json(404, {"error": "not found"})

        def _serve_file(self, path, name, mime):
            from urllib.parse import quote
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
            self.send_header("Content-Disposition",
                             "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                             % (ascii_name, quote(name)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/api/open":
                room = (self._read_json().get("room") or "").strip()
                if not room:
                    return self._json(400, {"error": "room required"})
                state.want_open(room)
                worker.put("open", room)
                self._json(200, {"ok": True, "room": room})
            elif self.path == "/api/send":
                text = self._read_json().get("text") or ""
                if not state.has_target():
                    return self._json(400, {"error": "no room open"})
                if not text.strip():
                    return self._json(400, {"error": "empty text"})
                worker.put("send", text)
                self._json(200, {"ok": True})
            elif self.path.split("?", 1)[0] == "/api/sendfile":
                if not state.has_target():
                    return self._json(400, {"error": "no room open"})
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0:
                    return self._json(400, {"error": "empty body"})
                name = ""
                if "?" in self.path:
                    q = parse_qs(self.path.split("?", 1)[1])
                    name = (q.get("name") or [""])[0]
                safe = _safe_name(name)
                dest = os.path.join(tmpdir, f"{int(time.time() * 1000)}_{safe}")
                remaining = n
                with open(dest, "wb") as f:  # stream — never buffer a whole video
                    while remaining > 0:
                        chunk = self.rfile.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                worker.put("sendfile", dest)
                self._json(200, {"ok": True, "name": safe})
            elif self.path == "/api/export":
                if not state.has_target():
                    return self._json(400, {"error": "no room open"})
                body = self._read_json()
                kind = body.get("kind")
                if kind not in ("photo", "video", "file"):
                    return self._json(400, {"error": "bad kind"})
                rank = body.get("rank")
                name = body.get("name") or ""
                import uuid
                xid = uuid.uuid4().hex
                state.export_pending(xid)
                worker.put("export", xid, kind, rank if isinstance(rank, int) else -1, name)
                self._json(202, {"id": xid})
            elif self.path == "/api/rooms":
                worker.put("rooms")
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})

    return Handler


def serve(worker: Worker, host: str, port: int, tmpdir: str = None) -> ThreadingHTTPServer:
    """Build the server (worker already startable). Caller runs serve_forever."""
    state = State()
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="kakao-webui-")
    threading.Thread(target=_pump, args=(worker, state), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(worker, state, tmpdir))
    httpd.daemon_threads = True
    return httpd


def run(serial=None, addr=None, adb="adb", host="127.0.0.1", port=8800):
    dev = connect(serial, addr, adb=adb)
    tmpdir = tempfile.mkdtemp(prefix="kakao-webui-")
    worker = Worker(dev, export_dir=tmpdir)
    worker.start()
    httpd = serve(worker, host, port, tmpdir=tmpdir)
    shown = host if host not in ("0.0.0.0", "") else "<vm-ip>"
    print(f"kakao-cli webui on http://{shown}:{port}  (Ctrl-C to stop)")
    if host in ("0.0.0.0", ""):
        print("  WARNING: bound to all interfaces with no auth — anyone on the "
              "network can control this KakaoTalk account.")
        print("  Prefer the default 127.0.0.1 + an SSH tunnel: "
              f"ssh -L {port}:127.0.0.1:{port} <vm>")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        worker.put("quit")


# --- the single page: TUI tone & manner (monospace, dark, reverse-video bars),
# responsive down to phones (single-screen list↔conversation) ---
PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#16161e">
<title>kakao-cli</title>
<style>
  :root{
    --bg:#16161e; --fg:#c0caf5; --dim:#565f89; --bar:#2a2e42; --barfg:#c0caf5;
    --sel:#3d59a1; --selfg:#ffffff; --accent:#7aa2f7; --unread:#e0af68;
    --line:#2a2e42; --me:#9ece6a;
    --safe-b:env(safe-area-inset-bottom,0px);
    --safe-t:env(safe-area-inset-top,0px);
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--fg);
    font-family:"JetBrains Mono","DejaVu Sans Mono","D2Coding",Menlo,Consolas,monospace;
    font-size:14px;line-height:1.5;display:flex;flex-direction:column;
    height:100vh;height:100dvh;overflow:hidden}
  button{font:inherit;color:inherit;background:transparent;border:none;cursor:pointer}
  .bar{background:var(--bar);color:var(--barfg);padding:4px 10px;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;flex:0 0 auto}
  .bar .k{color:var(--accent)}
  #header{padding-top:calc(4px + var(--safe-t))}
  .main{flex:1 1 auto;display:flex;min-height:0}
  .list{flex:0 0 32%;max-width:360px;min-width:200px;overflow-y:auto;
    border-right:1px solid var(--line);padding:4px 0;-webkit-overflow-scrolling:touch}
  .row{padding:3px 10px;cursor:pointer;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis}
  .row:hover{background:var(--line)}
  .row.sel{background:var(--sel);color:var(--selfg)}
  .row.unread{font-weight:bold;color:var(--unread)}
  .row.sel.unread{color:var(--selfg)}
  .row .badge{color:var(--unread);font-weight:bold}
  .row.sel .badge{color:var(--selfg)}
  .pane{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;position:relative}
  .title{display:flex;align-items:center;gap:6px;padding:4px 12px;color:var(--accent);
    font-weight:bold;border-bottom:1px solid var(--line)}
  .title #titletext{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
  .back{display:none;color:var(--accent);font-weight:bold;padding:4px 8px;
    min-width:36px;border-radius:4px}
  .back:hover{background:var(--line)}
  .msgs{flex:1 1 auto;overflow-y:auto;padding:6px 12px;-webkit-overflow-scrolling:touch}
  .msg{white-space:pre-wrap;word-break:break-word;padding:1px 0}
  .msg.media{color:var(--me)}
  .msg.dl{cursor:pointer;display:block;width:100%;text-align:left}
  .msg.dl:hover{text-decoration:underline}
  .msg .dlico{color:var(--accent)}
  .msg.dl-busy{opacity:.55}
  .msg.dl-busy .dlico::after{content:" …"}
  .newmsg{position:absolute;left:50%;transform:translateX(-50%);bottom:64px;
    display:none;background:var(--sel);color:var(--selfg);border-radius:14px;
    padding:4px 12px;box-shadow:0 1px 6px rgba(0,0,0,.4);z-index:5}
  .newmsg.show{display:block}
  .inputline{display:flex;align-items:center;gap:6px;padding:4px 12px;
    border-top:1px solid var(--line);padding-bottom:calc(4px + var(--safe-b))}
  .inputline .prompt{color:var(--accent)}
  .inputline.blur .prompt{color:var(--dim)}
  .attach{color:var(--accent);font-weight:bold;user-select:none;
    min-width:32px;text-align:center}
  .attach:hover{color:var(--unread)}
  .inputline #input{flex:1;background:transparent;border:none;color:var(--fg);
    font-family:inherit;font-size:16px;outline:none;min-width:0}
  .send{color:var(--accent);font-weight:bold;padding:4px 10px;border-radius:4px}
  .send:hover{background:var(--line)}
  .empty{color:var(--dim);padding:8px 12px}

  /* phones / narrow: single-screen navigation (list <-> conversation) */
  @media (max-width:720px){
    #header{display:none}
    .list{flex:1 1 auto;max-width:none;min-width:0;border-right:none}
    .pane{flex:1 1 auto}
    .row{min-height:44px;display:flex;align-items:center}
    .back{display:inline-block}
    .attach{min-width:44px;min-height:44px;line-height:44px;font-size:22px}
    .send{min-height:44px;padding:0 14px}
    .title{min-height:44px}
    body[data-view="list"] .pane{display:none}
    body[data-view="convo"] .list{display:none}
  }
  @media (hover:none){ .msg.dl:active{opacity:.6} .row:active{background:var(--line)} }
</style></head>
<body data-view="list">
  <div class="bar" id="header"></div>
  <div class="main">
    <div class="list" id="list" role="listbox" aria-label="채팅방 목록"></div>
    <div class="pane">
      <div class="title bar" id="title">
        <button class="back" id="back" aria-label="목록으로">←</button>
        <span id="titletext">(select a room)</span>
      </div>
      <div class="msgs" id="msgs" role="log" aria-live="polite"><div class="empty">—</div></div>
      <button class="newmsg" id="newmsg" aria-label="새 메시지로 이동">새 메시지 ↓</button>
      <div class="inputline blur" id="inputline">
        <button class="attach" id="attach" title="파일/이미지 첨부" aria-label="첨부">+</button>
        <input type="file" id="file" hidden>
        <span class="prompt">&gt;</span>
        <input id="input" type="text" autocomplete="off" spellcheck="false"
               enterkeyhint="send"
               placeholder="메시지 입력 후 Enter">
        <button class="send" id="send">보내기</button>
      </div>
    </div>
  </div>
  <div class="bar" id="status" role="status">starting …</div>
<script>
"use strict";
const HINT = '<span class="k">kakao-cli</span> — ' +
  '<span class="k">Tab</span>: focus  ' +
  '<span class="k">↑↓/Enter</span>: room  ' +
  '<span class="k">Enter</span>: send  ' +
  '<span class="k">Esc</span>: list  ' +
  '<span class="k">r</span>: refresh  ' +
  '<span class="k">+</span>: file';
const $ = id => document.getElementById(id);
$("header").innerHTML = HINT;

let st = {seq:-1, rooms:[], room:null, messages:[], status:""};
let sel = 0, focus = "list", view = "list";
const input = $("input");

const esc = s => (s||"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const isMobile = () => window.matchMedia("(max-width:720px)").matches;

// ---- mobile view switching (list <-> conversation) with browser history ----
function setView(v){
  view = v;
  document.body.dataset.view = v;
  $("back").setAttribute("aria-hidden", v==="convo" ? "false" : "true");
}
function enterConvo(){
  if(isMobile()){
    setView("convo");
    history.pushState({view:"convo"}, "");
  }
}
$("back").addEventListener("click", ()=>{
  if(history.state && history.state.view==="convo") history.back();
  else setView("list");
});
window.addEventListener("popstate", ()=>{ if(isMobile()) setView("list"); });

// ---- rendering: touch DOM only when the relevant slice actually changed ----
let prevRoomsKey="", prevSelKey="", prevMsgsKey="", prevStatus="", prevTitle="";

function roomRow(r,i){
  const div = document.createElement("div");
  div.className = "row" + (r.unread?" unread":"");
  div.setAttribute("role","option");
  const badge = r.unread ? ' <span class="badge">('+r.unread+')</span>' : '';
  div.innerHTML = esc(r.name)+badge;
  div.onclick = ()=>{ sel=i; openSel(); };
  return div;
}
function renderRooms(){
  const key = JSON.stringify(st.rooms.map(r=>[r.name,r.unread]));
  if(key!==prevRoomsKey){
    prevRoomsKey = key;
    const list = $("list");
    list.innerHTML = "";
    st.rooms.forEach((r,i)=> list.appendChild(roomRow(r,i)));
    prevSelKey = "";              // force selection repaint
  }
  const selKey = sel+"/"+focus;
  if(selKey!==prevSelKey){
    prevSelKey = selKey;
    const list = $("list");
    [...list.children].forEach((el,i)=>{
      const on = (i===sel && focus==="list");
      el.classList.toggle("sel", on);
      el.setAttribute("aria-selected", on?"true":"false");
    });
    const selEl = list.children[sel];
    if(selEl && focus==="list") selEl.scrollIntoView({block:"nearest"});
  }
}

function msgNode(msg, rank){
  const kind = (msg && msg.kind) || "text";
  const dl = (kind==="photo" || kind==="video" || kind==="file");
  if(dl){
    const b = document.createElement("button");
    b.className = "msg media dl";
    const label = kind==="file" ? (msg.name || msg.text || "[File]") : msg.text;
    b.innerHTML = '<span class="dlico">⤓</span> ' + esc(label);
    b.title = "탭해서 받기";
    b.onclick = () => startExport(kind, rank, msg.name||"", b);
    return b;
  }
  const div = document.createElement("div");
  div.className = "msg";
  div.textContent = (msg && msg.text) || "";
  return div;
}
function renderMessages(){
  const key = JSON.stringify(st.messages);
  if(key===prevMsgsKey) return;
  const m = $("msgs");
  // messages are the *visible* window on the device, so a new message shifts the
  // window rather than purely appending — follow the tail only if the reader was
  // already at the bottom; otherwise keep their position and flag new content.
  const firstLoad = (prevMsgsKey==="");
  const wasBottom = firstLoad || (m.scrollHeight - m.scrollTop - m.clientHeight) < 48;
  const keepTop = m.scrollTop;
  prevMsgsKey = key; prevMsgsLen = st.messages.length;
  if(!st.messages.length){ m.innerHTML = '<div class="empty">—</div>'; hideNew(); return; }
  // ranks per downloadable kind (stable across the whole list)
  const ranks = {};
  const frag = document.createDocumentFragment();
  st.messages.forEach(msg=>{
    const kind=(msg&&msg.kind)||"text";
    let rank=0; if(kind==="photo"||kind==="video"||kind==="file"){ rank=ranks[kind]||0; ranks[kind]=rank+1; }
    frag.appendChild(msgNode(msg, rank));
  });
  m.replaceChildren(frag);   // note: this resets scrollTop to 0
  if(wasBottom){ m.scrollTop = m.scrollHeight; hideNew(); }
  else { m.scrollTop = keepTop; showNew(); }
}
let prevMsgsLen = 0;
function showNew(){ $("newmsg").classList.add("show"); }
function hideNew(){ $("newmsg").classList.remove("show"); }
$("newmsg").addEventListener("click", ()=>{ const m=$("msgs"); m.scrollTop=m.scrollHeight; hideNew(); });
$("msgs").addEventListener("scroll", ()=>{
  const m=$("msgs");
  if((m.scrollHeight - m.scrollTop - m.clientHeight) < 48) hideNew();
});

function render(){
  renderRooms();
  const title = st.room ? ("— "+st.room+" —") : "(select a room)";
  if(title!==prevTitle){ prevTitle=title; $("titletext").textContent = title; }
  renderMessages();
  $("inputline").className = "inputline" + (focus==="input" ? "" : " blur");
  const status = " " + (st.status||"");
  if(status!==prevStatus){ prevStatus=status; $("status").textContent = status; }
}

function openSel(){
  const r = st.rooms[sel];
  if(!r) return;
  focus = "list";
  post("/api/open", {room:r.name});
  enterConvo();
  prevMsgsKey = "";                 // new room: force a message repaint
  if(!isMobile()){ focus="input"; input.focus(); }
  render();
}

// receive a media/file bubble: Save it off the device, then download in-browser
async function startExport(kind, rank, name, el){
  if(el.classList.contains("dl-busy")) return;
  el.classList.add("dl-busy");
  try{
    const r = await fetch("/api/export", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({kind, rank, name})});
    if(!r.ok){ el.classList.remove("dl-busy"); return; }
    const id = (await r.json()).id;
    for(let i=0;i<90;i++){
      await new Promise(res=>setTimeout(res, 1000));
      const s = await fetch("/api/export/"+id);
      if(!s.ok) continue;
      const js = await s.json();
      if(js.ready){ window.location = "/api/download/"+js.token; break; }
      if(js.error) break;
    }
  }catch(e){ /* status reflects worker result via poll */ }
  el.classList.remove("dl-busy");
}

async function post(path, body){
  try{
    await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body||{})});
  }catch(e){ /* transient */ }
}

async function poll(){
  try{
    const r = await fetch("/api/state?since="+st.seq);
    if(r.status===204) return;
    if(r.ok){ st = await r.json(); render(); }
  }catch(e){ /* keep trying */ }
}

function sendCurrent(){
  const t = input.value;
  if(t.trim()){ post("/api/send", {text:t}); input.value=""; }
}

// keyboard — mirror the TUI key map (desktop); input stays usable on mobile
document.addEventListener("keydown", (e)=>{
  if(e.target===input){
    if(e.key==="Tab"){ e.preventDefault(); focus="list"; input.blur(); render(); }
    else if(e.key==="Escape"){ focus="list"; input.blur(); render(); if(isMobile()) $("back").click(); }
    else if(e.key==="Enter"){ e.preventDefault(); sendCurrent(); }
    return;                       // single keys (r, arrows) must not fire while typing
  }
  if(e.key==="Tab"){ e.preventDefault(); focus="input"; input.focus(); render(); }
  else if(e.key==="ArrowUp"){ e.preventDefault(); sel=Math.max(0,sel-1); render(); }
  else if(e.key==="ArrowDown"){ e.preventDefault(); sel=Math.min(st.rooms.length-1,sel+1); render(); }
  else if(e.key==="Enter"){ e.preventDefault(); openSel(); }
  else if(e.key==="r"){ post("/api/rooms"); }
});
input.addEventListener("focus", ()=>{ if(focus!=="input"){ focus="input"; render(); }});
$("send").addEventListener("click", ()=>{ sendCurrent(); input.focus(); });

// file attach: + opens the picker, selection uploads to the open room
$("attach").addEventListener("click", ()=>{
  if(!st.room){ st.status = " 먼저 방을 선택하세요"; prevStatus=""; render(); return; }
  $("file").click();
});
$("file").addEventListener("change", async (e)=>{
  const f = e.target.files[0];
  if(!f || !st.room) return;
  st.status = " attaching " + f.name + " …"; prevStatus=""; render();
  try{
    await fetch("/api/sendfile?name=" + encodeURIComponent(f.name), {method:"POST", body:f});
  }catch(err){ /* status via poll */ }
  e.target.value = "";
});

// keep the input line visible when the mobile keyboard opens (visualViewport)
function fitViewport(){
  const vv = window.visualViewport;
  if(vv) document.body.style.height = vv.height + "px";
}
if(window.visualViewport){
  window.visualViewport.addEventListener("resize", fitViewport);
  window.visualViewport.addEventListener("scroll", fitViewport);
  fitViewport();
}

poll();
setInterval(poll, 1000);
</script>
</body></html>
"""

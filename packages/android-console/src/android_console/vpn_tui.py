"""Non-blocking VPN panel, reusable inside a curses app or on its own."""
from __future__ import annotations
import curses
import locale
import queue
import threading
import time
import webbrowser
from .vpn import ConsoleVPNClient, VPNError, safe_auth_url


class VPNPanel:
    def __init__(self, client):
        self.client, self.events = client, queue.Queue()
        self.data, self.error, self.busy, self.selected = {}, '', False, 0

    def request(self, action='status', node=None):
        if self.busy:
            return
        self.busy = True
        def work():
            try:
                self.events.put((self.client.call(action, node), ''))
            except VPNError as e:
                self.events.put((None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def consume(self):
        try:
            data, error = self.events.get_nowait()
        except queue.Empty:
            return False
        if data is not None:
            previous = self.data.get('exit_nodes', [])
            selected_id = previous[self.selected]['id'] if self.selected < len(previous) else None
            self.data = data
            nodes = data.get('exit_nodes', [])
            self.selected = next((i for i, n in enumerate(nodes) if n['id'] == selected_id),
                                 next((i for i, n in enumerate(nodes) if n['name'] == data.get('exit_node')),
                                      next((i for i, n in enumerate(nodes) if n['online']), 0)))
        self.error, self.busy = error, False
        self.selected = min(self.selected, max(0, len(self.data.get('exit_nodes', []))-1))
        return True

    def loop(self, scr):
        self.request()
        scr.keypad(True);scr.timeout(100)
        refresh, dirty = time.monotonic(), True
        while True:
            dirty |= self.consume()
            nodes = self.data.get('exit_nodes', [])
            if dirty:
                scr.erase();h,w=scr.getmaxyx()
                lines=['VM VPN — Linux VM only',
                       'L login · O open URL · Up/Down select · Enter apply',
                       'D direct · R refresh · Esc/Q close',
                       'Scope: '+self.data.get('hostname','Linux VM')+' (Mac network is unchanged)',
                       'State: '+self.data.get('state','loading')+'   Exit: '+str(self.data.get('exit_node') or 'direct'),
                       self.error or self.data.get('message','')]
                auth=safe_auth_url(self.data.get('auth_url'))
                if auth:lines += ['Open in your signed-in browser:',auth]
                lines += ['Exit nodes:']
                for y,line in enumerate(lines):
                    if y<h-1:
                        try:scr.addnstr(y,0,line,w-1)
                        except curses.error:pass
                for i,node in enumerate(nodes):
                    y=len(lines)+i
                    if y>=h-2:break
                    label=node['name']+('' if node['online'] else ' [offline]')
                    try:scr.addnstr(y,0,label,w-1,curses.A_REVERSE if i==self.selected else curses.A_NORMAL)
                    except curses.error:pass
                try:scr.addnstr(h-1,0,'Working…' if self.busy else 'Ready',w-1)
                except curses.error:pass
                scr.refresh();dirty=False
            try:key=scr.get_wch()
            except curses.error:key=None
            if key in ('q','Q','\x1b'):break
            if key in ('l','L'):self.request('login');dirty=True
            elif key in ('r','R'):self.request();dirty=True
            elif key in ('d','D'):self.request('direct');dirty=True
            elif key in ('o','O'):
                auth=safe_auth_url(self.data.get('auth_url'))
                if auth:webbrowser.open(auth)
            elif key in ('\n','\r',curses.KEY_ENTER) and nodes:
                if nodes[self.selected]['online']:self.request('exit',nodes[self.selected]['id']);dirty=True
            elif key==curses.KEY_UP:self.selected=max(0,self.selected-1);dirty=True
            elif key==curses.KEY_DOWN:self.selected=min(max(0,len(nodes)-1),self.selected+1);dirty=True
            elif key==curses.KEY_RESIZE:dirty=True
            if time.monotonic()-refresh>=2 and not self.busy:
                self.request();refresh=time.monotonic()


def run(base='http://127.0.0.1:8800', screen=None):
    locale.setlocale(locale.LC_ALL,'')
    panel=VPNPanel(ConsoleVPNClient(base))
    if screen is None:curses.wrapper(panel.loop)
    else:panel.loop(screen)
    return 0

"""Restricted Linux VM agent; Unix socket only, no auth-key or arbitrary commands."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import platform
import socketserver
import stat
import subprocess
import threading

from .vpn import VPNError, safe_auth_url


class TailscaleController:
    def __init__(self, hostname='kakao-lima', binary='/usr/bin/tailscale'):
        if platform.system() != 'Linux':
            raise VPNError('The VPN agent runs only in the Linux VM, never on macOS')
        self.hostname, self.binary = hostname, binary
        self.lock = threading.RLock()

    def _run(self, args, timeout=6):
        try:
            return subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise VPNError('VM Tailscale command unavailable or timed out') from e

    def _raw(self):
        result = self._run(['status', '--json'])
        try:
            raw = json.loads(result.stdout)
            if not isinstance(raw, dict):
                raise ValueError('not a status object')
            return raw
        except ValueError as e:
            raise VPNError('VM Tailscale 상태를 읽을 수 없습니다') from e

    def status(self, raw=None):
        raw = raw if raw is not None else self._raw()
        peers = raw.get('Peer') or {}
        nodes = [{'id': p['TailscaleIPs'][0], 'name': p.get('HostName') or p.get('DNSName') or p['TailscaleIPs'][0],
                  'online': bool(p.get('Online'))}
                 for p in peers.values() if p.get('ExitNodeOption') and p.get('TailscaleIPs')]
        selected = next((p.get('HostName') for p in peers.values() if p.get('ExitNode')), None)
        return {'ok': True, 'available': True, 'scope': 'linux-vm',
                'hostname': (raw.get('Self') or {}).get('HostName') or self.hostname,
                'state': raw.get('BackendState') or 'Unknown',
                'auth_url': safe_auth_url(raw.get('AuthURL')) if raw.get('BackendState') != 'Running' else '',
                'exit_node': selected, 'exit_nodes': sorted(nodes, key=lambda n:n['name'].lower()),
                'health': raw.get('Health') or []}

    def dispatch(self, action, node=None):
        with self.lock:
            raw = self._raw()
            if action == 'status':
                return self.status(raw)
            if action == 'login':
                if raw.get('BackendState') != 'Running' and not safe_auth_url(raw.get('AuthURL')):
                    # Timeout ends the CLI wait, not the daemon's pending enrollment.
                    result = self._run(['up', '--hostname=' + self.hostname, '--accept-dns=false', '--timeout=2s'], timeout=5)
                    raw = self._raw()
                    if result.returncode and raw.get('BackendState') != 'Running' and not safe_auth_url(raw.get('AuthURL')):
                        raise VPNError('VM Tailscale 인증을 시작하지 못했습니다. VM 서비스 로그를 확인하세요')
                return self.status(raw)
            if action not in ('exit', 'direct'):
                raise VPNError('Unknown VPN operation')
            if raw.get('BackendState') != 'Running':
                raise VPNError('먼저 VM Tailscale 로그인이 필요합니다')
            if action == 'exit':
                allowed = {p['TailscaleIPs'][0] for p in (raw.get('Peer') or {}).values()
                           if p.get('ExitNodeOption') and p.get('Online') and p.get('TailscaleIPs')}
                if not isinstance(node, str) or node not in allowed:
                    raise VPNError('온라인 Exit node 목록에서 선택하세요')
                args = ['set', '--exit-node=' + node, '--exit-node-allow-lan-access=true', '--accept-dns=true']
            else:
                args = ['set', '--exit-node=', '--exit-node-allow-lan-access=false', '--accept-dns=false']
            if self._run(args).returncode:
                raise VPNError('VM VPN 경로를 변경하지 못했습니다')
            return self.status()


def make_handler(controller):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Enrollment URLs and node details must not enter access logs.

        def do_POST(self):
            code, result = 200, None
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.path != '/' or not 0 < length <= 4096:
                    raise VPNError('Invalid VPN request')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or set(body) - {'action', 'node'}:
                    raise VPNError('Invalid VPN request')
                result = controller.dispatch(body.get('action'), body.get('node'))
            except (VPNError, ValueError, TypeError) as e:
                code, result = 400, {'ok': False, 'error': str(e)}
            data = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--socket', default='/run/kakao-network/control.sock')
    p.add_argument('--hostname', default='kakao-lima')
    args = p.parse_args(argv)
    controller = TailscaleController(args.hostname)
    path = Path(args.socket)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise VPNError('Refusing to replace a non-socket path')
        path.unlink()
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
    with Server(str(path), make_handler(controller)) as server:
        path.chmod(0o660)
        server.serve_forever()


if __name__ == '__main__':
    main()

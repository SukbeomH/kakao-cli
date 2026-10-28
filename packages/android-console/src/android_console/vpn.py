"""VM-only VPN management transports. Clients never invoke the host's Tailscale."""
from __future__ import annotations
import http.client
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from .errors import ConsoleError


class VPNError(ConsoleError):
    code = 'vpn_error'


def safe_auth_url(value):
    if not isinstance(value, str):
        return ''
    try:
        p = urlsplit(value)
        return value if p.scheme == 'https' and p.hostname == 'login.tailscale.com' and not p.username and not p.password and p.port in (None, 443) else ''
    except ValueError:
        return ''


class UnixAgentClient:
    def __init__(self, path):
        self.path = path

    def call(self, action='status', node=None):
        class Connection(http.client.HTTPConnection):
            def connect(conn):
                conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.sock.settimeout(conn.timeout)
                conn.sock.connect(self.path)
        conn = Connection('localhost', timeout=15)
        try:
            payload = json.dumps({'action': action, 'node': node}).encode()
            conn.request('POST', '/', payload, {'Content-Type': 'application/json'})
            response = conn.getresponse()
            data = json.loads(response.read(262145))
            if not isinstance(data, dict):
                raise ValueError('Invalid VPN response')
            if response.status != 200 or not data.get('ok'):
                raise VPNError(data.get('error', 'VM VPN operation failed'))
            return data
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise VPNError('VM VPN 관리 서비스에 연결할 수 없습니다') from e
        finally:
            conn.close()


class ConsoleVPNClient:
    def __init__(self, base='http://127.0.0.1:8800'):
        p = urlsplit(base)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
            raise VPNError('VPN console URL must be an http(s) origin without credentials')
        self.base = base.rstrip('/')

    def call(self, action='status', node=None):
        body = None if action == 'status' else json.dumps({'action': action, 'node': node}).encode()
        request = Request(self.base + '/api/vpn', data=body,
                          headers={'Content-Type': 'application/json', 'Origin': self.base})
        try:
            with urlopen(request, timeout=18) as response:
                result = json.loads(response.read(262145))
                if not isinstance(result, dict):
                    raise ValueError('Invalid VPN response')
        except HTTPError as e:
            try:
                data = json.loads(e.read(4096))
                message = data.get('error') if isinstance(data, dict) else None
            except ValueError:
                message = None
            raise VPNError(message or 'VM VPN request failed') from e
        except (OSError, ValueError, URLError) as e:
            raise VPNError('웹 콘솔에 연결할 수 없습니다: ' + self.base) from e
        if not result.get('ok'):
            raise VPNError(result.get('error', 'VM VPN operation failed'))
        return result


def add_parser(commands, default_url='http://127.0.0.1:8801'):
    parser = commands.add_parser('vpn', help='Manage the Linux VM VPN through its web console')
    actions = parser.add_subparsers(dest='vpn_action', required=True)
    for action in ('status', 'login', 'exit', 'direct', 'tui'):
        child = actions.add_parser(action)
        child.add_argument('--url', default=default_url, help='VM web console origin; never local macOS Tailscale')
        child.add_argument('--json', action='store_true')
        if action == 'exit':child.add_argument('node', help='Exit node IP from vpn status')
        if action == 'login':child.add_argument('--open', action='store_true', help='Open the enrollment URL in your browser')
        child.set_defaults(func=command)


def command(args):
    if args.vpn_action == 'tui':
        from .vpn_tui import run
        return run(args.url)
    result = ConsoleVPNClient(args.url).call(args.vpn_action, getattr(args, 'node', None))
    if getattr(args, 'open', False):
        url = safe_auth_url(result.get('auth_url'))
        if url:
            import webbrowser
            webbrowser.open(url)
    return result

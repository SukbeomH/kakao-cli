"""VM VPN boundaries: no host Tailscale or Android device is touched."""
from contextlib import contextmanager
import argparse
import copy
import http.client
import json
import socketserver
import threading
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from android_console import vpn, vpn_agent, vpn_tui, webui
from android_console.worker import Worker


RAW = {'BackendState': 'Running', 'Self': {'HostName': 'fixture-vm'}, 'Peer': {
    'a': {'HostName': 'online-exit', 'TailscaleIPs': ['100.64.0.1'], 'ExitNodeOption': True, 'Online': True},
    'b': {'HostName': 'offline-exit', 'TailscaleIPs': ['100.64.0.2'], 'ExitNodeOption': True, 'Online': False},
    'c': {'HostName': 'ordinary-peer', 'TailscaleIPs': ['100.64.0.3'], 'Online': True},
}}


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr(vpn_agent.platform, 'system', lambda: 'Linux')
    c = vpn_agent.TailscaleController()
    c._raw = Mock(return_value=copy.deepcopy(RAW))
    c._run = Mock(return_value=SimpleNamespace(returncode=0))
    return c


@pytest.mark.parametrize('value', [None, '', 'http://login.tailscale.com/a/x',
    'https://evil.test/a/x', 'https://login.tailscale.com.evil.test/a/x',
    'https://user:pass@login.tailscale.com/a/x', 'https://login.tailscale.com:8443/a/x',
    'javascript:alert(1)', 'https://login.tailscale.com:bad/a/x'])
def test_auth_links_are_official_https_only(value):
    assert vpn.safe_auth_url(value) == ''


def test_agent_refuses_macos(monkeypatch):
    monkeypatch.setattr(vpn_agent.platform, 'system', lambda: 'Darwin')
    with pytest.raises(vpn.VPNError, match='only in the Linux VM'):
        vpn_agent.TailscaleController()


def test_exit_whitelist_and_vm_only_flags(controller):
    result = controller.dispatch('exit', '100.64.0.1')
    assert len(result['exit_nodes']) == 2
    controller._run.assert_called_once_with(['set', '--exit-node=100.64.0.1',
        '--exit-node-allow-lan-access=true', '--accept-dns=true'])
    controller.dispatch('direct')
    controller._run.assert_called_with(['set', '--exit-node=',
        '--exit-node-allow-lan-access=false', '--accept-dns=false'])


@pytest.mark.parametrize('node', ['100.64.0.2', '100.64.0.3', '--reset', '100.64.0.1; touch /tmp/no', None, []])
def test_unknown_offline_or_arbitrary_nodes_rejected(controller, node):
    with pytest.raises(vpn.VPNError, match='온라인 Exit node'):
        controller.dispatch('exit', node)
    controller._run.assert_not_called()


def test_login_pending_and_failed_command(controller):
    pending = {'BackendState': 'NeedsLogin', 'AuthURL': 'https://login.tailscale.com/a/fixture'}
    controller._raw.return_value = pending
    assert controller.dispatch('login')['auth_url'] == pending['AuthURL']
    controller._run.assert_not_called()
    with pytest.raises(vpn.VPNError, match='로그인'):
        controller.dispatch('exit', '100.64.0.1')
    controller._raw.side_effect = [{'BackendState': 'NeedsLogin'}, pending]
    controller._run.return_value.returncode = 1  # normal enrollment wait timeout
    assert controller.dispatch('login')['auth_url'] == pending['AuthURL']
    controller._run.assert_called_with(['up', '--hostname=kakao-lima', '--accept-dns=false', '--timeout=2s'], timeout=5)
    controller._raw.side_effect = None
    controller._raw.return_value = {'BackendState': 'NeedsLogin'}
    with pytest.raises(vpn.VPNError, match='시작하지 못했습니다'):
        controller.dispatch('login')


def test_bad_status_and_set_failure(controller, monkeypatch):
    controller._run.return_value = SimpleNamespace(returncode=1, stdout='[]')
    with pytest.raises(vpn.VPNError, match='상태'):
        vpn_agent.TailscaleController._raw(controller)
    with pytest.raises(vpn.VPNError, match='경로'):
        controller.dispatch('direct')
    with pytest.raises(vpn.VPNError, match='Unknown'):
        controller.dispatch('logout')


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_unix_agent_real_transport(tmp_path, controller):
    # macOS AF_UNIX paths are limited to 104 bytes; pytest's tmpdir is longer.
    short = tempfile.TemporaryDirectory(dir='/tmp', prefix='vpn-')
    path = short.name + '/agent.sock'
    with short, running(socketserver.UnixStreamServer(path, vpn_agent.make_handler(controller))):
        client = vpn.UnixAgentClient(path)
        assert client.call()['scope'] == 'linux-vm'
        assert client.call('exit', '100.64.0.1')['state'] == 'Running'
        with pytest.raises(vpn.VPNError, match='Unknown'):
            client.call('arbitrary-command')
        with pytest.raises(vpn.VPNError, match='온라인'):
            client.call('exit', '--auth-key=secret')
    with pytest.raises(vpn.VPNError, match='서비스'):
        client.call()


@pytest.mark.parametrize('enabled', [True, False])
def test_console_api_opt_in_and_origin_guards(tmp_path, monkeypatch, enabled):
    worker = Worker(Mock())
    fake = Mock()
    fake.call.return_value = {'ok': True, 'available': True, 'scope': 'linux-vm', 'state': 'Running'}
    monkeypatch.setattr(webui, 'UnixAgentClient', Mock(return_value=fake))
    monkeypatch.delenv('ANDROID_CONSOLE_NETWORK_SOCKET', raising=False)
    server = webui.serve(worker, '127.0.0.1', 0, tmpdir=str(tmp_path), fs_root=str(tmp_path),
                         network_socket='/fixture/agent.sock' if enabled else None)
    with running(server):
        base = f'http://127.0.0.1:{server.server_port}'
        client = vpn.ConsoleVPNClient(base)
        assert client.call()['available'] is enabled
        if enabled:
            assert client.call('login')['state'] == 'Running'
            fake.call.assert_called_with('login', None)
        else:
            with pytest.raises(vpn.VPNError, match='not configured'):
                client.call('login')
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        try:
            cases = [({'action':'logout'}, {'Content-Type':'application/json'}, 400),
                     ({'action':'login', 'auth_key':'secret'}, {'Content-Type':'application/json'}, 400),
                     ({'action':'login'}, {'Content-Type':'application/json','Origin':'http://evil.test'}, 403),
                     ({'action':'login'}, {'Content-Type':'text/plain'}, 415)]
            for body, headers, expected in cases:
                conn.request('POST', '/api/vpn', json.dumps(body), headers)
                response = conn.getresponse(); response.read()
                assert response.status == expected
            conn.request('POST', '/api/vpn', 'x'*4097, {'Content-Type':'application/json'})
            response = conn.getresponse(); response.read()
            assert response.status == 413
            conn.request('GET', '/api/vpn', headers={'Host':'evil.test'})
            response = conn.getresponse(); response.read()
            assert response.status == 403
            if enabled:
                fake.call.side_effect = vpn.VPNError('VM agent offline')
                with pytest.raises(vpn.VPNError, match='offline'):
                    client.call('direct')
        finally:
            conn.close()


def test_cli_shared_transport_without_adb(monkeypatch, capsys):
    from kakao_cli import cli as kakao_cli
    from android_console import cli
    fake = Mock()
    fake.call.return_value = {'ok':True, 'available':True, 'state':'NeedsLogin',
                              'auth_url':'https://login.tailscale.com/a/fixture'}
    factory = Mock(return_value=fake)
    monkeypatch.setattr(vpn, 'ConsoleVPNClient', factory)
    monkeypatch.setattr(kakao_cli, '_receiver', Mock(side_effect=AssertionError('No device access')))
    import webbrowser
    opened = Mock()
    monkeypatch.setattr(webbrowser, 'open', opened)
    assert cli.main(['vpn','login','--json']) == 0
    factory.assert_called_with('http://127.0.0.1:8801')
    assert json.loads(capsys.readouterr().out)['state'] == 'NeedsLogin'
    opened.assert_not_called()
    assert kakao_cli.main(['vpn','login','--open','--json']) == 0
    factory.assert_called_with('http://127.0.0.1:8800')
    opened.assert_called_once_with('https://login.tailscale.com/a/fixture')
    assert cli.main(['vpn','exit','100.64.0.1','--url','http://vm.test:8800','--json']) == 0
    factory.assert_called_with('http://vm.test:8800')
    fake.call.assert_called_with('exit', '100.64.0.1')


def test_tui_does_not_block_and_recovers_after_error():
    release = threading.Event()
    started = threading.Event()
    class Client:
        def call(self, action, node):
            started.set(); assert release.wait(2)
            raise vpn.VPNError('VM not ready')
    panel = vpn_tui.VPNPanel(Client())
    panel.request('login')
    assert started.wait(1) and panel.busy
    assert panel.consume() is False
    panel.request('direct')  # busy requests do not overlap
    release.set()
    data, error = panel.events.get(timeout=1)
    panel.events.put((data,error))
    assert panel.consume() and not panel.busy and panel.error == 'VM not ready'


def test_tui_prefers_current_exit_and_preserves_selection():
    panel = vpn_tui.VPNPanel(Mock())
    nodes = [{'id':'100.64.0.1','name':'first','online':True},
             {'id':'100.64.0.2','name':'current','online':True}]
    panel.events.put(({'exit_node':'current','exit_nodes':nodes},''))
    assert panel.consume() and panel.selected == 1
    panel.selected = 0
    panel.events.put(({'exit_node':'current','exit_nodes':list(reversed(nodes))},''))
    assert panel.consume() and panel.selected == 1


def test_existing_chat_tui_opens_shared_panel_and_restores_polling(monkeypatch):
    from kakao_cli import tui
    monkeypatch.setattr(tui.curses, 'curs_set', Mock())
    opened = Mock(return_value=0)
    monkeypatch.setattr(vpn_tui, 'run', opened)
    worker, screen = Mock(), Mock()
    app = tui.TUI(worker, vpn_url='http://fixture.test:8800')
    monkeypatch.setattr(app, '_drain', lambda: False)
    monkeypatch.setattr(app, '_draw', lambda scr: None)
    screen.get_wch.side_effect = ['v','q']
    app.loop(screen)
    opened.assert_called_once_with('http://fixture.test:8800', screen=screen)
    assert screen.timeout.call_args_list == [((50,), {}), ((50,), {})]
    worker.put.assert_called_once_with('quit')

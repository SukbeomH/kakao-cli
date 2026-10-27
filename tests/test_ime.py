"""Native preedit transport, without installing onto any actual device."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
from unittest.mock import Mock

import pytest

from android_console.ime import ACTION, COMPONENT, CompositionInput, validate


def message(phase='update', text='한'):
    return dict(session='browser_1:1', phase=phase, text=text)


@pytest.mark.parametrize('body', [
    {'session': True, 'phase': 'start'}, {'session': 'other', 'phase': 'start'},
    {'session': 'x:1', 'phase': 'delete'}, message(text=['한']),
    message('start', 'not empty'), message(text='가' * 10923),
])
def test_composition_rejects_invalid_data(body):
    with pytest.raises(ValueError):
        validate(body)


def test_unicode_preedit_transport_is_one_ordered_request_without_clipboard():
    device = Mock()
    device.shell.return_value = 'Broadcast completed: result=200, data="android-console-ime/1"'
    composing = CompositionInput(device)
    composing.ready = True
    text = '한글🙂\n" $HOME `id` $(touch /tmp/never)'
    composing.send(message(text=text))
    device.shell.assert_called_once()
    args = shlex.split(device.shell.call_args.args[0])
    assert args[:6] == ['am', 'broadcast', '-p', 'org.androidconsole.ime', '-a', ACTION]
    assert json.loads(base64.b64decode(args[-1])) == message(text=text)
    assert device.shell.call_args.kwargs == {'timeout': 3}


def test_unacknowledged_update_is_never_replayed():
    device = Mock()
    device.shell.return_value = 'Broadcast completed: result=0'
    composing = CompositionInput(device)
    composing.ready = True
    with pytest.raises(RuntimeError):
        composing.send(message())
    device.shell.assert_called_once()


def test_empty_start_can_wait_for_ime_receiver_readiness(monkeypatch):
    device = Mock()
    device.shell.side_effect = ['Broadcast completed: result=0',
                               'Broadcast completed: result=409',
                               'Broadcast completed: result=200, data="android-console-ime/1"']
    composing = CompositionInput(device)
    monkeypatch.setattr(composing, '_prepare', lambda: None)
    monkeypatch.setattr('android_console.ime.time.sleep', lambda _: None)
    composing.send(message('start', ''))
    assert device.shell.call_count == 3
    payloads = [json.loads(base64.b64decode(shlex.split(c.args[0])[-1])) for c in device.shell.call_args_list]
    assert payloads == [message('start', '')] * 3


def test_prepare_preserves_a_users_other_selected_keyboard():
    device = Mock()
    device.shell.return_value = 'user.keyboard/.IME'
    with pytest.raises(RuntimeError, match='선택'):
        CompositionInput(device)._prepare()
    device._run.assert_not_called()
    assert device.shell.call_count == 1


def test_matching_installed_helper_is_not_reinstalled():
    from android_console import ime
    assets = Path(ime.__file__).with_name('ime_assets')
    digest = hashlib.sha256((assets / 'android-console-ime.apk').read_bytes()).hexdigest()
    assert digest == (assets / 'android-console-ime.sha256').read_text().strip()
    device = Mock()
    device.shell.side_effect = [COMPONENT, 'package:/data/app/console/base.apk',
                               digest + '  /data/app/console/base.apk', COMPONENT, '', '', COMPONENT]
    composing = CompositionInput(device)
    composing._prepare()
    assert composing.ready
    device._run.assert_not_called()
    assert not any('disable' in c.args[0] for c in device.shell.call_args_list)


def test_upgrading_selected_helper_disables_before_install_then_reselects():
    device = Mock()
    order = []
    replies = iter([COMPONENT, 'package:/data/app/console/base.apk', 'oldhash base.apk', '', COMPONENT, '', '', COMPONENT])
    device.shell.side_effect = lambda command: (order.append(command), next(replies))[1]
    device._run.side_effect = lambda *a, **kw: order.append('install')
    CompositionInput(device)._prepare()
    assert order.index('ime disable ' + COMPONENT) < order.index('install') < order.index('ime set ' + COMPONENT)


def test_first_install_waits_for_input_method_registration(monkeypatch):
    device = Mock()
    replies = iter(['com.android.adbkeyboard/.AdbIME', '', '', '', COMPONENT, '', '', COMPONENT])
    calls = []
    device.shell.side_effect = lambda command: (calls.append(command), next(replies))[1]
    monkeypatch.setattr('android_console.ime.time.sleep', lambda _: None)
    composing = CompositionInput(device)
    composing._prepare()
    assert composing.ready
    device._run.assert_called_once()
    assert calls.count('ime list -a -s') == 3
    assert calls.index('ime enable ' + COMPONENT) > calls.index('ime list -a -s')

"""Clipboard request/reply semantics with a real socket pair, without a VM."""
from contextlib import contextmanager
import socket
import struct
import threading
from unittest.mock import Mock

import pytest

from android_console.scrcpy import ScrcpySession, ScrcpyError


def packet(text):
    data = text.encode()
    return b"\x00" + struct.pack(">I", len(data)) + data


@contextmanager
def connected():
    session = ScrcpySession(Mock(), "fixture-server")
    client, peer = socket.socketpair()
    session.ctrl, session.connected = client, True
    reader = threading.Thread(target=session._read_ctrl, args=(client,), daemon=True)
    reader.start()
    try:
        yield session, peer
    finally:
        session.stop()
        peer.close()
        reader.join(timeout=1)


def test_explicit_clipboard_reads_include_unchanged_and_empty_text():
    with connected() as (session, peer):
        def server():
            for value in ("한글🙂", "한글🙂", ""):
                assert peer.recv(2) == b"\x08\x00"
                peer.sendall(packet(value))
        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()
        assert session.get_clipboard() == (1, "한글🙂")
        assert session.get_clipboard() == (1, "한글🙂")
        assert session.get_clipboard() == (2, "")
        server_thread.join(timeout=1)


def test_timed_out_explicit_read_does_not_return_cached_text():
    with connected() as (session, peer):
        session.clip_text = "old cached text"
        with pytest.raises(TimeoutError):
            session.get_clipboard(timeout=0.03)
        assert peer.recv(2) == b"\x08\x00"
        assert not session.connected and session._restart.is_set()


def test_unset_clipboard_poll_does_not_restart_video():
    with connected() as (session, peer):
        session.video = True
        with pytest.raises(TimeoutError):
            session.get_clipboard(timeout=0.03, _poll=True)
        assert peer.recv(2) == b"\x08\x00"
        assert session.connected and session.video and not session._restart.is_set()


def test_manual_read_reconnects_after_ambiguous_background_timeout():
    with connected() as (session, peer):
        session.clip_text = "stale"
        with pytest.raises(TimeoutError):
            session.get_clipboard(timeout=0.03, _poll=True)
        assert peer.recv(2) == b"\x08\x00"
        fresh_client, fresh_peer = socket.socketpair()
        def reconnect():
            assert session._restart.wait(1)
            with session._clip_changed:
                session.ctrl, session.connected = fresh_client, True
                session._clip_uncertain = False
                session._clip_changed.notify_all()
            threading.Thread(target=session._read_ctrl, args=(fresh_client,), daemon=True).start()
            assert fresh_peer.recv(2) == b"\x08\x00"
            fresh_peer.sendall(packet("fresh"))
        worker = threading.Thread(target=reconnect, daemon=True)
        worker.start()
        try:
            assert session.get_clipboard(timeout=1) == (1, "fresh")
            worker.join(timeout=1)
        finally:
            fresh_peer.close()


def test_local_clipboard_store_ack_updates_cache_without_copy_notification():
    with connected() as (session, peer):
        changed = Mock()
        session.on_change = changed
        def acknowledge():
            data = peer.recv(1024)
            kind, seq, paste, length = struct.unpack(">BqBi", data[:14])
            assert (kind, paste, data[14:].decode()) == (9, 0, "local 한글")
            assert len(data[14:]) == length
            peer.sendall(b"\x01" + struct.pack(">q", seq))
        worker = threading.Thread(target=acknowledge, daemon=True)
        worker.start()
        assert session.set_clipboard("local 한글", paste=False)
        assert session.clip() == (0, "local 한글")
        changed.assert_not_called()
        worker.join(timeout=1)


def test_failed_clipboard_send_clears_pending_ack():
    session = ScrcpySession(Mock(), "fixture-server")
    with pytest.raises(ScrcpyError):
        session.set_clipboard("text")
    assert not session._ack

"""Read-only screen capture must not block the ordered input worker."""
import threading
from types import SimpleNamespace

from android_console.worker import Worker
from android_console.screen import ScreenBuffer


def test_slow_capture_does_not_delay_text_or_key_completion():
    capturing, release = threading.Event(), threading.Event()
    operations = []
    def capture():
        capturing.set()
        assert release.wait(2)
        return b"fixture png"
    device = SimpleNamespace(screencap_bytes=capture,
                             type_text=lambda text: operations.append(("text", text)),
                             keyevent=lambda code: operations.append(("key", code)))
    screen = ScreenBuffer()
    screen.touch()
    worker = Worker(device, screen=screen)
    worker.start()
    try:
        assert capturing.wait(1)
        # The screen is still blocked; both operations must finish independently.
        worker.put_wait("screentext", "한글", timeout=.3)
        worker.put_wait("key", "enter", timeout=.3)
        assert not release.is_set()
        assert operations == [("text", "한글"), ("key", "KEYCODE_ENTER")]
    finally:
        worker.put("quit")
        release.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert not worker._capture_thread.is_alive()


def test_inactive_screen_does_not_capture():
    calls = []
    device = SimpleNamespace(screencap_bytes=lambda: calls.append("capture"))
    worker = Worker(device, screen=ScreenBuffer())
    worker.start()
    worker.put_wait("quit")
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert calls == []

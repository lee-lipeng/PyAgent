"""拒绝来自普通网页的浏览器桥 WebSocket Origin。"""

from types import SimpleNamespace

import pytest

from pyagent.tools.browser.bridge import BrowserBridge


class FakeSocket:
    remote_address = ("127.0.0.1", 1000)

    def __init__(self, origin=None):
        self.request = SimpleNamespace(
            headers={"Origin": origin} if origin else {}
        )
        self.request_headers = {}
        self.closed = False
        self.role = None

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code

    def __aiter__(self):
        async def empty_messages():
            if False:
                yield ""
        return empty_messages()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [
    "https://example.com",
    "http://127.0.0.1",
    "null",
    "chrome-extension://invalid",
])
async def test_rejects_untrusted_origins(origin):
    bridge = BrowserBridge()
    client = FakeSocket(origin)
    await bridge._on_client_connect(client)
    assert client.closed
    assert client.close_code == 1008
    assert not bridge._ws_clients


@pytest.mark.asyncio
async def test_accepts_chrome_extension_origin_and_native_client():
    bridge = BrowserBridge()
    extension_client = FakeSocket("chrome-extension://" + "a" * 32)
    native_client = FakeSocket()
    await bridge._on_client_connect(extension_client)
    await bridge._on_client_connect(native_client)
    assert not extension_client.closed
    assert not native_client.closed
    assert not bridge._ws_clients

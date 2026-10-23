"""浏览器 WebSocket 请求超时、取消及迟到消息的状态隔离测试。"""

import asyncio
import json

import pytest

from pyagent.tools.browser.bridge import BrowserBridge
from pyagent.tools.browser.exceptions import BrowserDeliveryError, BrowserTimeoutError


class FakeSocket:
    def __init__(self, on_send=None, error=None):
        self.on_send = on_send
        self.error = error

    async def send(self, payload):
        if self.error is not None:
            raise self.error
        if self.on_send is not None:
            self.on_send(json.loads(payload)["id"])


@pytest.mark.asyncio
async def test_browser_ignores_unsolicited_protocol_messages():
    bridge = BrowserBridge()
    for message_type in ("ack", "progress", "result", "error"):
        bridge._dispatch_message({"type": message_type, "id": "unknown"})
    assert not bridge._acks
    assert not bridge._progress
    assert not bridge._results


@pytest.mark.asyncio
async def test_browser_accepts_result_that_arrives_during_send():
    bridge = BrowserBridge()

    def answer(exec_id):
        bridge._dispatch_message({"type": "ack", "id": exec_id})
        bridge._dispatch_message({"type": "result", "id": exec_id, "data": 42})

    bridge._ws_clients.add(FakeSocket(on_send=answer))
    result = await bridge._execute_ws("request", "42", "tab", timeout=0.1)
    assert result.data == 42
    assert not bridge._active_exec_ids
    assert not bridge._acks
    assert not bridge._results


@pytest.mark.asyncio
async def test_browser_cancel_cleans_state_and_ignores_late_result():
    bridge = BrowserBridge()
    bridge._ws_clients.add(FakeSocket())
    task = asyncio.create_task(
        bridge._execute_ws("request", "42", "tab", timeout=20)
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    bridge._dispatch_message({"type": "result", "id": "request", "data": 42})
    assert not bridge._active_exec_ids
    assert not bridge._acks
    assert not bridge._results


@pytest.mark.asyncio
async def test_browser_timeout_cleans_state_and_ignores_late_ack():
    bridge = BrowserBridge()
    bridge._ws_clients.add(FakeSocket())
    with pytest.raises(BrowserDeliveryError):
        await bridge._execute_ws("request", "42", "tab", timeout=0.04)

    bridge._dispatch_message({"type": "ack", "id": "request"})
    assert not bridge._active_exec_ids
    assert not bridge._acks
    assert not bridge._results


@pytest.mark.asyncio
async def test_browser_send_failure_does_not_leave_active_request():
    bridge = BrowserBridge()
    bridge._ws_clients.add(FakeSocket(error=ConnectionError("发送失败")))
    with pytest.raises(ConnectionError, match="发送失败"):
        await bridge._execute_ws("request", "42", "tab", timeout=0.1)
    assert not bridge._active_exec_ids


@pytest.mark.asyncio
async def test_browser_ack_without_result_raises_timeout_and_cleans_state():
    bridge = BrowserBridge()
    bridge._ws_clients.add(
        FakeSocket(on_send=lambda exec_id: bridge._dispatch_message(
            {"type": "ack", "id": exec_id}
        ))
    )
    with pytest.raises(BrowserTimeoutError):
        await bridge._execute_ws("request", "42", "tab", timeout=0.04)
    assert not bridge._active_exec_ids
    assert not bridge._acks


@pytest.mark.asyncio
async def test_disconnect_interrupts_inflight_browser_request():
    bridge = BrowserBridge()
    bridge._ws_clients.add(FakeSocket())
    pending = asyncio.create_task(
        bridge._execute_ws("pending", "42", "tab", timeout=60)
    )
    await asyncio.sleep(0.02)
    await bridge.disconnect()

    from pyagent.tools.browser.exceptions import BrowserNotConnectedError

    with pytest.raises(BrowserNotConnectedError, match="断开"):
        await asyncio.wait_for(pending, timeout=0.5)
    assert not bridge._active_exec_ids
    assert not bridge._results

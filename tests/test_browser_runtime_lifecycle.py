"""Runtime 自动连接浏览器的后台任务应随 teardown 一起终止。"""

import asyncio
from types import SimpleNamespace

import pytest

from pyagent.config.settings import Settings
from pyagent.core.runtime import Runtime


class FakeBridge:
    def __init__(self):
        self.settings = SimpleNamespace(auto_launch_chrome=False, chrome_path=None)
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def connect(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()

    def has_clients(self):
        return False


def configure_bridge(monkeypatch):
    bridge = FakeBridge()
    closed = []

    async def close_bridge():
        assert bridge.stopped.is_set() or not bridge.started.is_set()
        closed.append(True)

    monkeypatch.setattr("pyagent.tools.browser.get_bridge", lambda: bridge)
    monkeypatch.setattr("pyagent.tools.browser.close_bridge", close_bridge)
    return bridge, closed


@pytest.mark.asyncio
async def test_teardown_cancels_background_connection(monkeypatch):
    bridge, closed = configure_bridge(monkeypatch)
    runtime = Runtime(Settings())
    await runtime._init_browser_bridge()
    await asyncio.wait_for(bridge.started.wait(), timeout=1)

    await runtime.teardown()
    assert bridge.stopped.is_set()
    assert runtime._browser_start_task is None
    assert closed == [True]


@pytest.mark.asyncio
async def test_teardown_before_background_task_runs(monkeypatch):
    bridge, closed = configure_bridge(monkeypatch)
    runtime = Runtime(Settings())
    await runtime._init_browser_bridge()
    await runtime.teardown()
    assert runtime._browser_start_task is None
    assert closed == [True]


@pytest.mark.asyncio
async def test_duplicate_init_does_not_spawn_duplicate_connection_tasks(monkeypatch):
    bridge, closed = configure_bridge(monkeypatch)
    runtime = Runtime(Settings())
    await runtime._init_browser_bridge()
    await bridge.started.wait()
    task = runtime._browser_start_task

    await runtime._init_browser_bridge()
    assert runtime._browser_start_task is task

    await runtime.teardown()
    assert closed == [True]


@pytest.mark.asyncio
async def test_teardown_cancels_background_chrome_launch(monkeypatch):
    bridge, closed = configure_bridge(monkeypatch)
    bridge.settings.auto_launch_chrome = True

    async def connect_immediately():
        return None

    bridge.connect = connect_immediately
    launched = asyncio.Event()
    cancelled = asyncio.Event()

    async def launch_chrome(_path):
        launched.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        "pyagent.tools.browser.chrome_launcher.async_launch_chrome_if_needed",
        launch_chrome,
    )
    runtime = Runtime(Settings())
    await runtime._init_browser_bridge()
    await asyncio.wait_for(launched.wait(), timeout=1)
    await runtime.teardown()

    assert cancelled.is_set()
    assert runtime._browser_start_task is None
    assert closed == [True]

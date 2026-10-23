"""启动或结束 Hook 异常时 Runtime 必须释放运行状态。"""

import pytest

from pyagent.config.settings import Settings
from pyagent.core.loop import LoopResult
from pyagent.core.runtime import Runtime
from pyagent.hooks.types import EventType
from pyagent.session.types import Session


class FakeLoop:
    async def run(self, query, session, on_chunk=None, ctx=None):
        return LoopResult(success=True, stop_reason="completed")


@pytest.mark.asyncio
async def test_start_hook_error_cleans_running_state():
    runtime = Runtime(Settings(session={"enabled": False}, log={"file_enabled": False}))
    runtime._initialized = True
    runtime.loop = FakeLoop()

    def deny(_event):
        raise PermissionError("拒绝启动")

    runtime.hooks.on(EventType.AGENT_START, deny)
    with pytest.raises(PermissionError, match="拒绝启动"):
        await runtime.run("test", Session.create_new("safe"))
    assert not runtime.is_running
    assert runtime._current_ctx is None


@pytest.mark.asyncio
async def test_end_hook_error_cleans_running_state():
    runtime = Runtime(Settings(session={"enabled": False}, log={"file_enabled": False}))
    runtime._initialized = True
    runtime.loop = FakeLoop()

    def deny(_event):
        raise PermissionError("拒绝结束")

    runtime.hooks.on(EventType.AGENT_END, deny)
    result = await runtime.run("test", Session.create_new("safe"))
    assert result.success
    assert not runtime.is_running
    assert runtime._current_ctx is None

"""同一个 Runtime 不能并发覆盖活跃 Session 和 Hook 状态。"""

import asyncio

import pytest

from pyagent.config.settings import Settings
from pyagent.core.loop import LoopResult
from pyagent.core.runtime import Runtime
from pyagent.session.types import Session


@pytest.mark.asyncio
async def test_runtime_rejects_concurrent_runs():
    runtime = Runtime(Settings(session={"enabled": False}, log={"file_enabled": False}))
    runtime._initialized = True
    started = asyncio.Event()
    resume = asyncio.Event()

    class FakeLoop:
        async def run(self, query, session, on_chunk=None, ctx=None):
            started.set()
            await resume.wait()
            return LoopResult(success=True, stop_reason="completed")

    runtime.loop = FakeLoop()
    first = asyncio.create_task(runtime.run("first", Session.create_new("first")))
    await started.wait()
    try:
        with pytest.raises(RuntimeError, match="并发"):
            await runtime.run("second", Session.create_new("second"))
    finally:
        resume.set()

    assert (await first).success

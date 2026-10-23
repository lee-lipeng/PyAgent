"""并行工具异常时，不应留下仍在运行的兄弟任务。"""

import asyncio

import pytest

from pyagent.hooks.manager import HookManager
from pyagent.llm.types import ToolCall
from pyagent.tools.executor import ToolExecutor
from pyagent.tools.registry import ToolRegistry


class PendingTool:
    name = "pending"
    execution_mode = "parallel"

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    def validate_args(self, args):
        return args

    async def execute(self, tool_call_id, args, signal=None):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


@pytest.mark.asyncio
async def test_parallel_failure_reaps_other_tasks():
    tool = PendingTool()
    registry = ToolRegistry()
    registry.register(tool)
    hooks = HookManager()
    executor = ToolExecutor(registry, hooks)

    from pyagent.hooks.types import EventType

    async def forbid(event):
        if event.get("tool_call_id") == "deny":
            await tool.started.wait()
            raise PermissionError("denied")

    hooks.on(EventType.BEFORE_TOOL, forbid)
    with pytest.raises(PermissionError, match="denied"):
        await asyncio.wait_for(
            executor.execute_batch([
                ToolCall(id="pending", name="pending", arguments={}),
                ToolCall(id="deny", name="pending", arguments={}),
            ]),
            timeout=1,
        )
    assert tool.cancelled.is_set()

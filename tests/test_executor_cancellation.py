"""工具权限 Hook 和 Abort 的执行语义回归测试。"""

import asyncio

import pytest

from pyagent.hooks.manager import HookManager
from pyagent.hooks.types import EventType, HookControl
from pyagent.llm.types import ToolCall
from pyagent.tools.base import ToolResult
from pyagent.tools.executor import ToolExecutor
from pyagent.tools.registry import ToolRegistry


class RecordingTool:
    name = "record"
    execution_mode = "parallel"

    def __init__(self, on_execute=None):
        self.calls = []
        self.on_execute = on_execute

    def validate_args(self, args):
        return args

    async def execute(self, tool_call_id, args, signal=None):
        self.calls.append((tool_call_id, args))
        if self.on_execute:
            self.on_execute()
        return ToolResult(content="已执行")


def make_executor(tool=None, mode="parallel"):
    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)
    hooks = HookManager()
    return ToolExecutor(registry, hooks, mode=mode), hooks


@pytest.mark.asyncio
async def test_permission_cancel_overrides_prior_tool_result():
    tool = RecordingTool()
    executor, hooks = make_executor(tool)
    hooks.on(EventType.BEFORE_TOOL, lambda event: ToolResult(content="缓存成功结果"))
    hooks.on(EventType.BEFORE_TOOL, lambda event: HookControl(cancel=True, reason="无权限"))

    result = await executor.execute_batch([ToolCall(id="one", name="record")])

    assert len(result) == 1
    assert result[0].is_error
    assert result[0].details["error"] == "hook_cancelled"
    assert result[0].content == "无权限"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_pre_cancelled_signal_skips_before_hook_and_tool():
    tool = RecordingTool()
    executor, hooks = make_executor(tool)
    events = []
    hooks.on(EventType.BEFORE_TOOL, lambda event: events.append("before"))
    hooks.on(EventType.AFTER_TOOL, lambda event: events.append("after"))
    signal = asyncio.Event()
    signal.set()

    result = await executor.execute_batch([ToolCall(id="one", name="record")], signal)

    assert result[0].details["error"] == "aborted"
    assert result[0].is_error
    assert events == ["after"]
    assert tool.calls == []


@pytest.mark.asyncio
async def test_abort_during_before_hook_skips_tool():
    tool = RecordingTool()
    executor, hooks = make_executor(tool)
    signal = asyncio.Event()

    async def abort_before_tool(event):
        signal.set()

    hooks.on(EventType.BEFORE_TOOL, abort_before_tool)
    result = await executor.execute_batch([ToolCall(id="one", name="record")], signal)

    assert result[0].details["error"] == "aborted"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_sequential_abort_skips_remaining_tools_but_keeps_results():
    signal = asyncio.Event()
    tool = RecordingTool(on_execute=signal.set)
    executor, _ = make_executor(tool, mode="sequential")
    calls = [ToolCall(id=str(i), name="record") for i in range(3)]

    results = await executor.execute_batch(calls, signal)

    assert len(results) == len(calls)
    assert len(tool.calls) == 1
    assert not results[0].is_error
    assert [result.details.get("error") for result in results[1:]] == ["aborted", "aborted"]


@pytest.mark.asyncio
async def test_unblocked_hook_result_still_replaces_tool():
    tool = RecordingTool()
    executor, hooks = make_executor(tool)
    hooks.on(EventType.BEFORE_TOOL, lambda event: ToolResult(content="缓存结果"))
    result = await executor.execute_batch([ToolCall(id="one", name="record")])

    assert result[0].content == "缓存结果"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_non_cancelled_tool_runs_normally():
    tool = RecordingTool()
    executor, _ = make_executor(tool)
    result = await executor.execute_batch([ToolCall(id="one", name="record", arguments={"k": 1})])

    assert len(tool.calls) == 1
    assert tool.calls[0] == ("one", {"k": 1})
    assert result[0].content == "已执行"

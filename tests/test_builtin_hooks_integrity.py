"""内置 Hook 对工具终止信号和上下文使用量的处理。"""

import pytest

from pyagent.hooks.builtin import (
    setup_tool_result_truncation_hook,
    setup_usage_tracking_hook,
)
from pyagent.hooks.manager import HookManager
from pyagent.hooks.types import Event, EventType
from pyagent.llm.token_estimator import ContextUsage
from pyagent.session.types import Session
from pyagent.tools.base import ToolResult


@pytest.mark.asyncio
async def test_truncation_preserves_termination_and_error_metadata():
    hooks = HookManager()
    setup_tool_result_truncation_hook(hooks, max_chars=100)
    original = ToolResult(
        content="a" * 500,
        is_error=True,
        terminate=True,
        details={"source": "shell"},
    )

    outcome = await hooks.dispatch(Event(type=EventType.AFTER_TOOL), initial=original)
    result = outcome.value

    assert result.content != original.content
    assert result.is_error
    assert result.terminate
    assert result.details["source"] == "shell"
    assert result.details["truncated"] is True
    assert result.details["original_length"] == 500


@pytest.mark.asyncio
async def test_truncation_noop_keeps_original_result():
    hooks = HookManager()
    setup_tool_result_truncation_hook(hooks, max_chars=100)
    original = ToolResult(content="short", terminate=True)

    outcome = await hooks.dispatch(Event(type=EventType.AFTER_TOOL), initial=original)

    assert outcome.value is original


@pytest.mark.asyncio
async def test_usage_records_exact_input_without_adding_output_to_context():
    hooks = HookManager()
    session = Session.create_new("usage")
    usage = ContextUsage(limit=1000)
    loop = type("Loop", (), {"context_usage": usage})()
    setup_usage_tracking_hook(hooks, lambda: session, loop)

    await hooks.dispatch(
        Event(
            type=EventType.AFTER_LLM,
            payload={"usage": {"input_tokens": 120, "output_tokens": 80}},
        )
    )

    assert session.metadata.total_input_tokens == 120
    assert session.metadata.total_output_tokens == 80
    assert usage.used == 120
    assert usage.last_usage.input_tokens == 120
    assert usage.last_usage.output_tokens == 80


@pytest.mark.asyncio
async def test_usage_without_context_tracker_is_safe():
    hooks = HookManager()
    session = Session.create_new("no-tracker")
    loop = type("Loop", (), {"context_usage": None})()
    setup_usage_tracking_hook(hooks, lambda: session, loop)

    await hooks.dispatch(
        Event(
            type=EventType.AFTER_LLM,
            payload={"usage": {"input_tokens": 8, "output_tokens": 4}},
        )
    )

    assert session.metadata.total_input_tokens == 8
    assert session.metadata.total_output_tokens == 4

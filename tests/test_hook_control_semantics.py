"""HookControl(cancel=False) 不得跳过后续权限及改写处理器。"""

import pytest

from pyagent.hooks.manager import HookManager
from pyagent.hooks.types import Event, EventType, HookControl


@pytest.mark.asyncio
async def test_non_cancelling_hook_does_not_skip_permission_check():
    hooks = HookManager()
    hooks.on(EventType.BEFORE_TOOL, lambda event: HookControl(cancel=False))
    hooks.on(
        EventType.BEFORE_TOOL,
        lambda event: HookControl.cancel_with("工具无权限"),
    )

    result = await hooks.dispatch(
        Event(type=EventType.BEFORE_TOOL), initial={"path": "file.txt"}
    )

    assert result.cancelled
    assert result.cancel_reason == "工具无权限"
    assert result.value == {"path": "file.txt"}


@pytest.mark.asyncio
async def test_non_cancelling_hook_preserves_transform_chain():
    hooks = HookManager()
    hooks.on(EventType.BEFORE_TOOL, lambda event: {"a": 1})
    hooks.on(EventType.BEFORE_TOOL, lambda event: HookControl(cancel=False))
    hooks.on(
        EventType.BEFORE_TOOL,
        lambda event: {**event.get("value"), "b": 2},
    )

    result = await hooks.dispatch(Event(type=EventType.BEFORE_TOOL), initial={})

    assert not result.cancelled
    assert result.value == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_true_cancel_still_skips_later_handlers():
    hooks = HookManager()
    called = []
    hooks.on(
        EventType.BEFORE_TOOL,
        lambda event: HookControl.cancel_with("拒绝执行"),
    )
    hooks.on(EventType.BEFORE_TOOL, lambda event: called.append(True))

    result = await hooks.dispatch(
        Event(type=EventType.BEFORE_TOOL), initial="original"
    )

    assert result.cancelled
    assert result.value == "original"
    assert not called

"""Hook 装饰器。

提供 @hook 装饰器，用于以声明式方式定义事件处理器。
配合 HookManager.register_decorated() 使用。

用法::

    @hook(EventType.BEFORE_TOOL)
    async def log_tool(event: Event):
        logger.info("工具调用: %s", event.get("tool_name"))

    # 注册到 HookManager
    hooks.register_decorated(log_tool)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyagent.hooks.types import EventType


def hook(
    event_type: EventType,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """装饰器：标记一个函数为指定事件类型的 handler。

    Args:
        event_type: 要订阅的事件类型。

    Returns:
        装饰器函数，给原函数附加 _hook_type 属性。
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._hook_type = event_type
        return func

    return decorator

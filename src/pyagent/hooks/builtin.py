"""内置 Hook：Logging / Permission。

这些是横切关注点的默认实现，全部通过订阅事件工作，
不修改任何业务代码（Executor / Agent / Loop）。

用法::

        from pyagent.hooks.builtin import setup_logging_hooks

        hooks = HookManager()
        setup_logging_hooks(hooks, logger)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyagent.hooks.types import Event, EventType

if TYPE_CHECKING:
    from pyagent.hooks.manager import HookManager


def setup_logging_hooks(hooks: HookManager, logger: logging.Logger) -> None:
    """注册日志 Hook：在关键节点输出日志。"""
    async def on_agent_start(event: Event) -> None:
        logger.debug("Agent 启动")

    async def on_agent_end(event: Event) -> None:
        logger.debug("Agent 结束")

    async def on_before_tool(event: Event) -> None:
        logger.debug(f"调用工具: {event.payload.get('tool_name')} 参数: {event.payload.get('args')}")

    async def on_after_tool(event: Event) -> None:
        is_error = event.payload.get("is_error", False)
        if is_error:
            logger.warning(f"工具 {event.payload.get('tool_name')} 调用失败")
        else:
            logger.debug(f"工具 {event.payload.get('tool_name')} 调用完成")

    hooks.on(EventType.AGENT_START, on_agent_start)
    hooks.on(EventType.AGENT_END, on_agent_end)
    hooks.on(EventType.BEFORE_TOOL, on_before_tool)
    hooks.on(EventType.AFTER_TOOL, on_after_tool)


def setup_permission_hooks(hooks: HookManager, blocked_tools: set[str]) -> None:
    """注册权限 Hook：拦截被禁用的工具。

    在 BEFORE_TOOL 事件中检查工具名，如果被禁用则抛 PermissionError。
    ToolExecutor 的调用方需要 catch 这个异常来决定如何处理。
    """

    async def on_before_tool(event: Event) -> None:
        tool_name = event.get("tool_name", "")
        if tool_name in blocked_tools:
            raise PermissionError(f"工具 '{tool_name}' 被禁用")

    hooks.on(EventType.BEFORE_TOOL, on_before_tool)

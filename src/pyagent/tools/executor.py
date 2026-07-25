"""ToolExecutor — 工具执行器。

核心职责：
1. 从 registry 获取工具
2. 校验参数
3. 执行工具（parallel 或 sequential）
4. 执行前后 dispatch 事件到 HookManager

事件流：
    BEFORE_TOOL → execute() → AFTER_TOOL
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pyagent.hooks.types import Event, EventType
from pyagent.tools.base import ToolResult
from pyagent.tools.exceptions import ToolNotFoundError
from pyagent.tools.registry import ToolRegistry
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    from pyagent.hooks.manager import HookManager
    from pyagent.llm.types import ToolCall

logger = get_logger(__name__)


class ToolExecutor:
    """工具执行器。

    Args:
        registry: 工具注册表。
        hooks: 事件总线（HookManager），执行前后 dispatch 事件。
        mode: 默认执行模式，"parallel" 或 "sequential"。
              可被单个工具的 execution_mode 覆盖。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        hooks: HookManager,
        mode: str = "parallel",
    ) -> None:
        self._registry = registry
        self._hooks = hooks
        self._mode = mode

    async def execute_batch(
        self,
        tool_calls: list[ToolCall],
        signal: asyncio.Event | None = None,
    ) -> list[ToolResult]:
        """批量执行工具调用。

        根据 mode 决定并行或串行执行。
        如果批次中任一工具的 execution_mode 为 "sequential"，整批串行执行。

        Args:
            tool_calls: LLM 返回的工具调用列表。
            signal: 取消信号（abort），透传给每个工具的 execute()。

        Returns:
            与 tool_calls 等长的结果列表，顺序对应。
        """
        if not tool_calls:
            return []

        # 检查是否需要强制串行
        force_sequential = self._mode == "sequential"
        if not force_sequential:
            for tc in tool_calls:
                tool = self._registry.get(tc.name) if self._registry.has(tc.name) else None
                if tool is not None and tool.execution_mode == "sequential":
                    force_sequential = True
                    break

        if force_sequential:
            results = [await self._exec_one(tc, signal) for tc in tool_calls]
        else:
            results = await asyncio.gather(
                *[self._exec_one(tc, signal) for tc in tool_calls],
                return_exceptions=False,
            )
            results = list(results)

        return results

    async def _exec_one(
        self,
        tool_call: ToolCall,
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        """执行单个工具调用。

        流程：
        1. dispatch BEFORE_TOOL（Handler 可取消、返回 ToolResult 替代、或改写 args）
        2. 查找工具 → 校验参数 → 执行（透传 signal）
        3. dispatch AFTER_TOOL（Handler 可改写返回结果）
        4. 异常时返回 is_error=True 的 ToolResult

        Hook 协议（统一）：
        - return HookControl(cancel=True)        → 触发 cancelled 分支
        - return ToolResult(...)                 → 直接作为本工具的返回值
        - return dict (新 args)                  → 替换入参
        - return None                            → 不影响当前值（默认 args）
        """
        event = Event(
            type=EventType.BEFORE_TOOL,
            payload={
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "args": tool_call.arguments,
            },
        )
        # 初始值 = 当前 args；handler 返回 ToolResult 表示彻底替换工具执行。
        result = await self._hooks.dispatch(event, initial=tool_call.arguments)

        # 1) handler 直接返回 ToolResult → 完全替代工具执行
        if isinstance(result.value, ToolResult):
            return await self._emit_after(tool_call, result.value, result.value.is_error)

        # 2) handler 取消 → 返回错误 ToolResult
        if result.cancelled:
            cancelled_result = ToolResult(
                content=result.cancel_reason or f"工具 {tool_call.name} 已被 Hook 取消",
                is_error=True,
                details={"error": "hook_cancelled"},
            )
            return await self._emit_after(tool_call, cancelled_result, is_error=True)

        # 3) 否则把 result.value（可能已被改写 args）当作 args 继续执行
        tool_call.arguments = result.value

        # 查找工具
        try:
            tool = self._registry.get(tool_call.name)
        except ToolNotFoundError:
            not_found = ToolResult(
                content=f"工具不存在: {tool_call.name}",
                is_error=True,
                details={"error": "tool_not_found", "tool_name": tool_call.name},
            )
            return await self._emit_after(tool_call, not_found, is_error=True)

        # 校验参数
        try:
            validated_args = tool.validate_args(tool_call.arguments)
        except Exception as exc:
            validation_err = ToolResult(
                content=f"参数校验失败: {exc}",
                is_error=True,
                details={"error": "validation_error", "exception": str(exc)},
            )
            return await self._emit_after(tool_call, validation_err, is_error=True)

        # 执行（透传 cancel signal，工具可监听 signal 提前终止）
        try:
            exec_result = await tool.execute(tool_call.id, validated_args, signal=signal)
        except Exception as exc:
            logger.exception("工具执行异常: %s", tool_call.name)
            exec_result = ToolResult(
                content=f"工具执行出错: {exc}",
                is_error=True,
                details={"error": "execution_error", "exception": str(exc)},
            )

        return await self._emit_after(tool_call, exec_result, exec_result.is_error)

    async def _emit_after(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        is_error: bool,
    ) -> ToolResult:
        """派发 AFTER_TOOL，并允许 Hook 链式修改工具结果。"""
        event = Event(
            type=EventType.AFTER_TOOL,
            payload={
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "is_error": is_error,
            },
        )
        final = await self._hooks.dispatch(event, initial=result)
        # Hook 返回 None 时保留 result；返回 ToolResult 时替换；
        # 返回 HookControl 在 AFTER_TOOL 没业务意义，安全降级为 result
        if isinstance(final.value, ToolResult):
            return final.value
        return result

    @staticmethod
    def _make_event(event_type: str, **payload: Any) -> Any:
        """构造 Event 对象。延迟导入避免循环依赖。"""
        from pyagent.hooks.types import Event, EventType

        return Event(type=EventType(event_type), payload=payload)

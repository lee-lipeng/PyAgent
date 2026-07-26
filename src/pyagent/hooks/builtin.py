"""内置 Hook：Logging / Permission / Usage / TurnCount / DuplicateGuard / Truncation / AutoSave。

横切关注点的默认实现，全部通过订阅事件工作，
不修改任何业务代码（Executor / Agent / Loop）。

用法::

        from pyagent.hooks.builtin import (
            setup_logging_hooks,
            setup_permission_hooks,
            setup_usage_tracking_hook,
            setup_turn_counting_hook,
            setup_duplicate_tool_call_guard,
            setup_tool_result_truncation_hook,
            setup_auto_save_hook,
        )

        hooks = HookManager()
        setup_logging_hooks(hooks, logger)
        setup_usage_tracking_hook(hooks, lambda: current_session)
        ...

每个 setup_* 函数都是独立的，可单独启用或关闭，
关闭时只需不调用即可，不影响其它 hook。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pyagent.hooks.types import Event, EventType
from pyagent.tools.base import ToolResult

if TYPE_CHECKING:
    from pyagent.hooks.manager import HookManager
    from pyagent.session.store import SessionStore
    from pyagent.session.types import Session


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

    在 BEFORE_TOOL 事件中检查工具名，如果被禁用则返回 HookControl(cancel=True)。
    与抛出 PermissionError 相比，统一使用 HookControl 让取消路径可观察、可测试，
    也方便审计（HookControl.reason 直接说明原因）。
    """

    async def on_before_tool(event: Event):
        tool_name = event.get("tool_name", "")
        if tool_name in blocked_tools:
            # 短路返回取消信号，Executor 会构造一个 is_error ToolResult 继续派发 AFTER_TOOL
            from pyagent.hooks.types import HookControl

            return HookControl(cancel=True, reason=f"工具 '{tool_name}' 被禁用")

    hooks.on(EventType.BEFORE_TOOL, on_before_tool)


def setup_usage_tracking_hook(
        hooks: HookManager,
        session_getter: Callable[[], Session | None],
        loop: Any
) -> None:
    """注册 Token 用量聚合 Hook：自动累加 LLM 调用产生的 token。

    监听 AFTER_LLM 事件，从 payload.usage 读取 input/output tokens 后调用
    session.add_usage()。

    Args:
        hooks: HookManager。
        session_getter: 返回当前 session 的无参 callable。
            必须在注册时延迟求值（每次 LLM 后取最新 session），
            而非捕获某个具体 Session 实例（Runtime 每次 run 都会切换 session）。
        loop: AgentLoop对象，当LLM调用后若返回了具体的usage则更新当前上下文为此精确值。
    """

    async def on_after_llm(event: Event) -> None:
        session = session_getter()
        if session is None:
            return
        usage = event.payload.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        if input_tokens or output_tokens:
            session.add_usage(input_tokens, output_tokens)
            loop.context_usage._used = input_tokens + output_tokens

    hooks.on(EventType.AFTER_LLM, on_after_llm)


def setup_turn_counting_hook(
        hooks: HookManager,
        session_getter: Callable[[], Session | None],
) -> None:
    """注册轮次计数 Hook：每轮 BEFORE_LLM 自增 turn_count。

    统一 dispatch API 下 BEFORE_LLM 每个 turn 只触发一次，
    因此 Handler 直接 ++ 即可，无需去重逻辑。

    Args:
        hooks: HookManager。
        session_getter: 返回当前 session 的无参 callable。
    """

    async def on_before_llm(event: Event) -> None:
        session = session_getter()
        if session is None:
            return
        session.increment_turn()

    hooks.on(EventType.BEFORE_LLM, on_before_llm)


def setup_duplicate_tool_call_guard(
        hooks: HookManager,
        threshold: int = 3,
) -> Callable[[], None]:
    """注册重复工具调用守卫 Hook：阻止 LLM 陷入死循环。

    同一 (tool_name, args 哈希) 连续触发次数达到threshold时，
    BEFORE_TOOL 短路返回 HookControl(cancel=True)，
    Executor 会构造 is_error=True 的 ToolResult 让 LLM 看到失败信号。

    计数仅对"与上一次完全一致"的调用累加，参数任一字段变化即清零，
    避免误判"同一工具、相似但不同的多次调用"。

    Args:
        hooks: HookManager。
        threshold: 同一调用重复 N 次后拦截，默认 3。

    Returns:
        reset 函数，外部可在 session 切场景时调用清空统计。
    """
    counter: Counter[str] = Counter()
    last_fingerprint: dict[str, str] = {}

    def _fingerprint(args: Any) -> str:
        # 同一参数序列化得到稳定哈希，避免 JSON key 顺序差异导致误判
        try:
            normalized = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except Exception:
            normalized = repr(args)
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    async def on_before_tool(event: Event) -> Any:
        tool_name = event.payload.get("tool_name", "")
        if not tool_name:
            return None
        fp = _fingerprint(event.payload.get("args"))

        # 仅当与上一次完全一致时才计数（参数变化即清零）
        if last_fingerprint.get(tool_name) != fp:
            counter[tool_name] = 0
            last_fingerprint[tool_name] = fp

        counter[tool_name] += 1
        if counter[tool_name] >= threshold:
            from pyagent.hooks.types import HookControl

            return HookControl(
                cancel=True,
                reason=f"工具 '{tool_name}' 连续 {threshold} 次相同调用，已拦截避免死循环",
            )

    unsub = hooks.on(EventType.BEFORE_TOOL, on_before_tool)

    def reset() -> None:
        """清空计数状态（用于多 session/多任务切换）。"""
        counter.clear()
        last_fingerprint.clear()

    # 把 unsubscribe 句柄挂在 reset 函数上，方便外部一次性管理生命周期
    # （reset() 本身是个 Callable，再附 _unsub 不会影响其调用语义）
    reset._unsub = unsub
    return reset


def setup_tool_result_truncation_hook(
        hooks: HookManager,
        max_chars: int = 8000,
) -> None:
    """注册工具结果截断 Hook：防止超长结果撑爆上下文窗口。

    通过 AFTER_TOOL transform 链式修改 ToolResult，
    超过 ``max_chars`` 时：
        - 保留头部 + 尾部各 ``max_chars // 4`` 字符
        - 中间插入省略说明，告知 LLM 结果已被截断

    适用场景：read/grep/find/bash 类工具的输出经常超过模型可接受范围，
    不截断会导致下一轮 LLM 调用失败或成本飙升。
    """

    half = max_chars // 4

    async def on_after_tool(event: Event) -> ToolResult | None:
        # 通过 event.payload["value"] 注入当前 ToolResult
        result: ToolResult | None = event.payload.get("value")
        if result is None or not result.content:
            return None
        if len(result.content) <= max_chars:
            return None

        head = result.content[:half]
        tail = result.content[-half:]
        truncated = (
            f"{head}\n\n... [内容已截断，共 {len(result.content)} 字符，仅保留前 {half} 与后 {half} 字符] ...\n\n{tail}"
        )
        # 构造新的 ToolResult 替换原对象，保持 details 透传
        return ToolResult(
            content=truncated,
            is_error=result.is_error,
            details={
                **(result.details or {}),
                "truncated": True,
                "original_length": len(result.content),
            },
        )

    hooks.on(EventType.AFTER_TOOL, on_after_tool)


def setup_auto_save_hook(
        hooks: HookManager,
        session_store: SessionStore | None,
        session_getter: Callable[[], Session | None],
) -> None:
    """注册会话自动落盘 Hook：每次 LLM 调用前把当前会话写到磁盘。

    Args:
        hooks: HookManager。
        session_store: SessionStore 实例，传 None 表示禁用（ephemeral 场景下
            SessionStore.save 本身也会跳过落盘，传 None 更直接）。
        session_getter: 返回当前 session 的无参 callable ——
            必须在注册时延迟求值（每次 LLM 前取最新 session），而非捕获
            某个具体 Session 实例，因为 Runtime 每次 run 都会切换 session。
    """
    if session_store is None:
        # 无持久化存储：注册一个空 hook 占位，便于上层统一控制"是否启用"
        async def _noop(_event: Event) -> None:
            return None

        hooks.on(EventType.BEFORE_LLM, _noop)
        return

    async def on_before_llm(event: Event) -> None:
        # 统一 dispatch API 下 BEFORE_LLM 每个 turn 只触发一次，无需去重。
        session = session_getter()
        if session is None:
            return
        try:
            session_store.save(session)
        except Exception as exc:
            # 自动落盘失败不应阻塞 Agent 运行，仅记录警告
            from pyagent.utils.logger import get_logger

            get_logger(__name__).warning("BEFORE_LLM 自动落盘失败: %s", exc)

    hooks.on(EventType.BEFORE_LLM, on_before_llm)


__all__ = [
    "setup_auto_save_hook",
    "setup_logging_hooks",
    "setup_permission_hooks",
    "setup_usage_tracking_hook",
    "setup_turn_counting_hook",
    "setup_duplicate_tool_call_guard",
    "setup_tool_result_truncation_hook",
]

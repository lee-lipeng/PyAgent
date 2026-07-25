"""HookManager — 统一事件分发器。

设计哲学
---------
HookManager 只暴露一个核心动作 ``dispatch``，内部按 handler 返回值自动
区分三种语义：

- 返回 ``HookControl(cancel=True)``   → 取消派发，结果的 ``cancelled=True``
- 返回其它非 None 值                  → 替换当前值（链式 transform）
- 返回 ``None``                       → 不影响，继续派发下一个 handler

Handler 协议（推荐）::

    async def my_hook(event: Event):
        if event.get("tool_name") == "blocked":
            return HookControl.cancel_with("工具被禁用")
        # 想改 messages 时直接返回新值
        return [*event.get("value", []), "injected"]

    hooks.on(EventType.BEFORE_LLM, my_hook)
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from pyagent.hooks.types import DispatchResult, Event, EventType, HookControl
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)
HookHandler = Callable[[Event], Any]


class HookManager:
    """按注册顺序执行 Hook，并提供中间件风格的派发语义。

    支持一次性 hook 注册与取消订阅，handler 同步/异步皆可。
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[HookHandler]] = defaultdict(list)

    def on(self, event_type: EventType, handler: HookHandler) -> Callable[[], None]:
        """注册 handler；重复注册同一函数不会产生重复执行。

        Args:
            event_type: 要订阅的事件类型。
            handler: 事件回调，签名 ``handler(event: Event) -> Any``。

        Returns:
            unsubscribe 函数，调用后移除该 handler。
        """
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)
        removed = False

        def unsubscribe() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def dispatch[T](
        self,
        event: Event,
        initial: T | None = None,
    ) -> DispatchResult[T]:
        """统一事件派发入口。

        按 handler 注册顺序遍历 ``self._handlers[event.type]``，
        每个 handler 同步 / 异步执行后根据返回值决定后续动作：

        - HookControl(cancel=True)：停止派发，结果标记 cancelled=True
        - 其它非 None：替换 current 并继续派发下一个 handler
        - None：不修改 current，继续派发下一个 handler

        所有 handler 抛出的 Exception（非PermissionError）都会被
        记录并吞掉，保证单点失败不中断整条链。

        Args:
            event: 事件对象。
            initial: 链初始值（无 handler 返回替换值时由它兜底）。

        Returns:
            DispatchResult，包含 cancelled / cancel_reason / value。
        """
        # 当前值会随 handler 返回值更新；handler 通过 event.payload["value"]
        # 读取上一环节结果（注意：handler 不应主动修改 payload，仅读取）。
        current: Any = initial
        event.payload["value"] = current

        for handler in tuple(self._handlers.get(event.type, ())):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    result = await result

                if isinstance(result, HookControl):
                    # 取消信号直接短路，不再执行后续 handler
                    return DispatchResult(
                        cancelled=result.cancel,
                        cancel_reason=result.reason,
                        value=current,
                    )
                if result is not None:
                    # 链式 transform：把返回值作为下一环节的 current
                    current = result
                    event.payload["value"] = current
            except PermissionError:
                # 权限错误向上抛出，让 Runtime 决定如何处理（abort / 降级）
                raise
            except Exception:
                logger.exception(f"事件处理器执行失败: type={event.type.value}")

        return DispatchResult(cancelled=False, cancel_reason="", value=current)
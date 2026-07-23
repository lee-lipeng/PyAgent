"""HookManager — 通用事件总线。

核心设计：
- emit(event): 触发事件，按注册顺序 await 所有 handler
- on(type, handler): 订阅事件，返回 unsubscribe 函数
- HookManager 不知道任何上游模块，只做事件分发

横切关注点（Logging / Permission）
通过订阅事件实现，不需要修改任何业务代码。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pyagent.hooks.types import Event, EventType
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# 事件处理器类型：接收 Event，返回 None（同步或异步）
HookHandler = Callable[[Event], Awaitable[None] | None]


class HookManager:
    """通用事件总线。

    用法::

        hooks = HookManager()

        # 订阅
        def on_tool_start(event: Event):
            print(f"工具开始: {event.get('tool_name')}")

        unsub = hooks.on(EventType.BEFORE_TOOL, on_tool_start)

        # 触发
        await hooks.emit(Event(
            type=EventType.BEFORE_TOOL,
            payload={"tool_name": "read_file", "args": {"path": "a.txt"}},
        ))

        # 取消订阅
        unsub()
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[HookHandler]] = defaultdict(list)

    def on(self, event_type: EventType, handler: HookHandler) -> Callable[[], None]:
        """订阅事件。

        Args:
            event_type: 要订阅的事件类型。
            handler: 事件处理器，可以是同步或异步函数。

        Returns:
            取消订阅的函数，调用后移除该 handler。
        """
        self._handlers[event_type].append(handler)
        handlers = self._handlers[event_type]

        def _unsubscribe() -> None:
            if handler in handlers:
                handlers.remove(handler)

        return _unsubscribe

    async def emit(self, event: Event) -> None:
        """触发事件，按注册顺序调用所有 handler。

        同步 handler 会被包装为协程，保证调用顺序一致。

        异常处理策略:
            - ``PermissionError`` 作为"控制信号"立即向上抛,
              不被吞,保证权限 hook 拦截工具调用;
            - 其他 ``Exception`` 记录日志后继续执行后续 handler,
              不影响事件传播。
        """
        handlers = self._handlers.get(event.type, [])
        if not handlers:
            return

        for handler in handlers:
            try:
                result = handler(event)
                # 同步 handler 返回 None，异步返回 coroutine
                if asyncio.iscoroutine(result):
                    await result
            except PermissionError:
                # 控制信号:权限 hook 拦截工具调用,需要让调用方 catch
                raise
            except Exception:
                logger.exception("事件处理器执行失败: type=%s", event.type.value)

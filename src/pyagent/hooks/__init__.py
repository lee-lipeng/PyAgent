"""Hook 系统：通用事件总线。

HookManager 是整个 Runtime 的 Event Bus。
它不知道任何上游模块（Tool、Agent、Loop），
只提供 emit + subscribe 两个核心操作。

依赖方向：HookManager ← ToolExecutor ← Agent ← Runtime

横切关注点（Logging / Permission）
全部是 Hook 的订阅者，不需要修改 Executor。

内置 Hook 由 ``pyagent.hooks.builtin`` 提供，Runtime 在 setup 时
会根据 ``Settings.hooks`` 自动注册；用户也可手动调用::

    from pyagent.hooks import (
        setup_logging_hooks,
        setup_permission_hooks,
    )
"""

from pyagent.hooks.builtin import (
    setup_logging_hooks,
    setup_permission_hooks,
)
from pyagent.hooks.decorators import hook
from pyagent.hooks.manager import HookManager
from pyagent.hooks.types import Event, EventType

__all__ = [
    "Event",
    "EventType",
    "HookManager",
    "hook",
    "setup_logging_hooks",
    "setup_permission_hooks",
]

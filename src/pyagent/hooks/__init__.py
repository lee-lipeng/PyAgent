"""Hook 系统：通用事件总线。

HookManager 是整个 Runtime 的 Event Bus。
它不知道任何上游模块（Tool、Agent、Loop），
只暴露一个核心动作：dispatch —— 由 handler 返回值决定取消 / 转换 / pass。

依赖方向：HookManager ← ToolExecutor ← Agent ← Runtime

横切关注点（Logging / Permission / Usage / DuplicateGuard / Truncation …）
全部是 Hook 的订阅者，不需要修改 Executor。

内置 Hook 由 ``pyagent.hooks.builtin`` 提供，Runtime 在 setup 时
会根据 ``Settings.hooks`` 自动注册；用户也可手动调用::

    from pyagent.hooks import (
        setup_logging_hooks,
        setup_permission_hooks,
        setup_usage_tracking_hook,
        setup_turn_counting_hook,
        setup_duplicate_tool_call_guard,
        setup_tool_result_truncation_hook,
    )
"""

from pyagent.hooks.builtin import (
    setup_auto_save_hook,
    setup_duplicate_tool_call_guard,
    setup_logging_hooks,
    setup_permission_hooks,
    setup_tool_result_truncation_hook,
    setup_turn_counting_hook,
    setup_usage_tracking_hook,
)
from pyagent.hooks.decorators import hook
from pyagent.hooks.manager import HookManager
from pyagent.hooks.types import DispatchResult, Event, EventType, HookControl

__all__ = [
    "DispatchResult",
    "Event",
    "EventType",
    "HookControl",
    "HookManager",
    "hook",
    "setup_auto_save_hook",
    "setup_logging_hooks",
    "setup_permission_hooks",
    "setup_usage_tracking_hook",
    "setup_turn_counting_hook",
    "setup_duplicate_tool_call_guard",
    "setup_tool_result_truncation_hook",
]

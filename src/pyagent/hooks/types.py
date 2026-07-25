"""事件类型定义。

EventType 覆盖整个 Runtime 生命周期的关键节点，
任何模块都可以 dispatch 事件，HookManager 不关心谁在 dispatch。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):  # noqa: UP042
    """事件类型枚举。

    命名规则：<模块>_<动作>，全大写下划线分隔。
    """

    # ── Agent 生命周期 ──
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    #: 用户在运行期间提交改向输入（steer）
    AGENT_STEER = "agent_steer"
    #: 用户中止当前运行（abort）
    AGENT_ABORT = "agent_abort"

    # ── LLM 调用 ──
    BEFORE_LLM = "before_llm"
    AFTER_LLM = "after_llm"
    LLM_REQUEST_ERROR = "llm_request_error"

    # ── Tool 执行 ──
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    TOOL_BATCH_START = "tool_batch_start"
    TOOL_BATCH_END = "tool_batch_end"

    # ── 上下文工程 ──
    # 压缩前触发：可通过 dispatch 取消压缩或修改 cut_point / previous_summary / prompt
    SESSION_BEFORE_COMPACT = "session_before_compact"
    # 压缩完成后触发
    SESSION_COMPACT = "session_compact"

    # ── 错误 ──
    ERROR = "error"


@dataclass(slots=True)
class HookControl:
    """Hook 派发控制信号。

    Handler 返回此对象表示"取消当前流程"，
    HookManager 收到后停止派发后续 handler 并把
    cancel / reason 透传到 ``DispatchResult``。

    用法::

        return HookControl(cancel=True, reason="工具被禁用")
        return HookControl.cancel_with("权限不足")
    """

    cancel: bool = True
    reason: str = ""

    @classmethod
    def cancel_with(cls, reason: str) -> HookControl:
        """构造一个取消信号，便于调用方一行写完。"""
        return cls(cancel=True, reason=reason)


@dataclass(slots=True)
class DispatchResult[T]:
    """HookManager.dispatch 的统一返回值。

    取代了旧的 "notify / transform / short_circuit" 三种语义分裂的 API，
    一次 dispatch 既能拿到链式转换的最终值，又能感知是否被取消。

    字段：
        cancelled: 是否被某个 handler 返回 ``HookControl(cancel=True)`` 终止。
        cancel_reason: 取消原因；未取消时为空字符串。
        value: 链式转换的最终值。
            - 所有 handler 返回 None → 等于初始值 ``initial``
            - 任一 handler 返回非 HookControl 的非 None → 该值替换 current 并继续
    """

    cancelled: bool = False
    cancel_reason: str = ""
    value: T | None = None


class Event(BaseModel):
    """事件载体。

    每个事件携带一个 type 和一个自由格式的 payload dict。
    payload 的具体字段由 dispatch 方约定，订阅方按需读取。
    """

    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """从 payload 中安全取值。"""
        return self.payload.get(key, default)

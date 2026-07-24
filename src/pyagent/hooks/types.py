"""事件类型定义。

EventType 覆盖整个 Runtime 生命周期的关键节点，
任何模块都可以 emit 事件，HookManager 不关心谁在 emit。
TODO: HOOK模块优化
"""

from __future__ import annotations

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

    # ── Tool 执行 ──
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"

    # ── 上下文工程 ──
    # 压缩前触发，Hook 可取消压缩或自定义摘要 prompt
    SESSION_BEFORE_COMPACT = "session_before_compact"
    # 压缩完成后触发
    SESSION_COMPACT = "session_compact"

    # ── 错误 ──
    ERROR = "error"


class Event(BaseModel):
    """事件载体。

    每个事件携带一个 type 和一个自由格式的 payload dict。
    payload 的具体字段由 emit 方约定，订阅方按需读取。
    """

    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """从 payload 中安全取值。"""
        return self.payload.get(key, default)

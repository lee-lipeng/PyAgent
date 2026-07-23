"""Token 估算器。

借鉴 Pi Agent 的 token 估算策略：
- 优先使用 LLM 返回的 usage 精确值
- 回退到 chars / 4 的启发式估算
- 提供上下文窗口溢出检测

估算层级（从精确到粗略）::

    Usage.input_tokens  ─精确─▶  ContextUsage.update()
                                          │
           chars / 4  ─粗略─▶  estimate_tokens()  ◀┘
                                          │
                              estimate_messages_tokens()
                                          │
                              calculate_context_tokens()

公开 API::

    estimate_tokens(text)              单字符串 token 估算
    estimate_messages_tokens(messages) 消息列表 token 估算
    calculate_context_tokens(...)       精确值优先，否则估算
    estimate_session_context(session)   session → token 数（CLI 便利函数）
    build_session_usage(session)        session → 已更新的 ContextUsage
    ContextUsage(limit, threshold)      上下文使用量监控器
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pyagent.llm.types import Usage
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

#: 启发式估算系数：约 4 个字符 ≈ 1 token（中英文混合的粗略值）
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """启发式估算文本的 token 数。

    粗略规则：字符数 / 4。对于没有 tokenizer 的场景够用。

    Args:
        text: 待估算的文本。

    Returns:
        估算的 token 数（向上取整）。
    """
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算消息列表的总 token 数。

    将每条消息序列化为 JSON 后按字符数估算，
    覆盖 content / tool_calls / tool_call_id 等所有字段。

    Args:
        messages: LLM 格式的消息字典列表。

    Returns:
        估算的总 token 数。
    """
    return sum(estimate_tokens(json.dumps(msg, ensure_ascii=False)) for msg in messages)


def calculate_context_tokens(
    messages: list[dict[str, Any]],
    last_usage: Usage | None = None,
) -> int:
    """计算当前上下文的 token 数。

    - 如果有最近一次 LLM 返回的 usage 且 input_tokens > 0，返回精确值
    - 否则回退到 :func:`estimate_messages_tokens`

    Args:
        messages: 当前消息列表。
        last_usage: 最近一次 LLM 调用返回的 usage（可选，精确值优先）。

    Returns:
        上下文 token 数。
    """
    if last_usage is not None and last_usage.input_tokens > 0:
        return last_usage.input_tokens
    return estimate_messages_tokens(messages)


class ContextUsage:
    """上下文使用量监控器。

    跟踪当前上下文的 token 使用情况，提供溢出/阈值检测和进度条格式化。

    用法::

        usage = ContextUsage(limit=128000)
        usage.update(messages, last_usage)
        if usage.is_overflow:
            trigger_compaction()

    Args:
        limit: 模型上下文窗口大小（token 数）。
        threshold: 压缩触发阈值（0~1），达到 ``limit * threshold`` 时触发压缩。
    """

    def __init__(self, limit: int = 128000, threshold: float = 0.8) -> None:
        self._limit = limit
        self._threshold = threshold
        self._used: int = 0
        self._last_usage: Usage | None = None

    @property
    def limit(self) -> int:
        """上下文窗口大小。"""
        return self._limit

    @property
    def used(self) -> int:
        """当前已用 token 数。"""
        return self._used

    @property
    def last_usage(self) -> Usage | None:
        """最近一次 LLM 调用返回的精确 usage（可能为 None）。"""
        return self._last_usage

    @property
    def threshold_tokens(self) -> int:
        """压缩触发阈值 token 数。"""
        return int(self._limit * self._threshold)

    @property
    def threshold(self) -> float:
        """压缩触发阈值（0~1）。"""
        return self._threshold

    @property
    def percentage(self) -> float:
        """使用百分比（0~1）。"""
        if self._limit == 0:
            return 0.0
        return self._used / self._limit

    @property
    def is_threshold_reached(self) -> bool:
        """是否达到压缩阈值。"""
        return self._used >= self.threshold_tokens

    @property
    def is_overflow(self) -> bool:
        """是否溢出（超过上下文窗口）。"""
        return self._used >= self._limit

    @property
    def remaining(self) -> int:
        """剩余 token 数。"""
        return max(0, self._limit - self._used)

    def update(
        self,
        messages: list[dict[str, Any]],
        last_usage: Usage | None = None,
    ) -> int:
        """更新当前 token 使用量。

        优先使用 ``last_usage.input_tokens``，否则回退到启发式估算。

        Args:
            messages: 当前消息列表。
            last_usage: 最近一次 LLM 调用的 usage（精确值优先）。

        Returns:
            更新后的 token 数。
        """
        self._last_usage = last_usage
        self._used = calculate_context_tokens(messages, last_usage)
        return self._used

    def reset(self) -> None:
        """重置使用量（压缩后调用）。"""
        self._used = 0
        self._last_usage = None

    def format_bar(self, width: int = 20) -> str:
        """格式化进度条字符串。

        用于 REPL / 日志显示，例::

            context: 45% [█████████░░░░░░░░░░░]

        Args:
            width: 进度条总宽度。

        Returns:
            形如 ``context: 45% [████████░░░░░░░░░░░░]`` 的字符串。
        """
        pct = min(1.0, self.percentage)
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        return f"context: {int(pct * 100)}% [{bar}]"


def estimate_session_context(session: Any) -> int:
    """估算 session 当前上下文的 token 数（便利函数）。

    用于 REPL ``/context`` 等只读场景，避免手写：
    ``estimate_messages_tokens(session.to_messages())``。

    Args:
        session: :class:`pyagent.session.types.Session` 对象。
    Returns:
        估算的 token 数。
    """
    if session is None:
        return 0
    # 延迟导入避免循环依赖
    from pyagent.session.messages import message_to_llm

    messages = [message_to_llm(m) for m in session.messages]
    # 如果有系统提示词元数据，估算时也带上
    if getattr(session, "metadata", None) and session.metadata.system_prompt:
        messages = [{"role": "system", "content": session.metadata.system_prompt}, *messages]
    return estimate_messages_tokens(messages)


def build_session_usage(
    session: Any,
    *,
    default_limit: int = 128000,
    default_threshold: float = 0.8,
) -> ContextUsage:
    """根据 session 元数据构造并 update() 一个 :class:`ContextUsage`。

    用于 REPL ``/context`` 等只读场景，避免手写
    ``ContextUsage(...) + usage.update(session.to_messages())``。

    Args:
        session: :class:`pyagent.session.types.Session` 对象。
        default_limit: session 未设置 ``context_window`` 时使用的窗口大小。
        default_threshold: session 未设置 ``compaction_threshold`` 时使用的阈值。

    Returns:
        已 update() 过的 ContextUsage 实例。
    """
    metadata = getattr(session, "metadata", None)
    limit = getattr(metadata, "context_window", 0) or default_limit
    threshold = getattr(metadata, "compaction_threshold", default_threshold) or default_threshold

    # 需要拿到原始 messages 列表给 ContextUsage.update
    if session is None:
        return ContextUsage(limit=limit, threshold=threshold)
    from pyagent.session.messages import message_to_llm

    messages = [message_to_llm(m) for m in session.messages]
    if metadata and getattr(metadata, "system_prompt", ""):
        messages = [{"role": "system", "content": metadata.system_prompt}, *messages]

    usage = ContextUsage(limit=limit, threshold=threshold)
    usage.update(messages)
    return usage

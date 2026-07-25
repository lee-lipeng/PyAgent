"""上下文构建。

借鉴 Pi Agent 的三阶段上下文构建设计：

1. Path 阶段：从会话中确定消息路径
2. ContextEntries 阶段：路径上的消息 + 压缩摘要 + retainedTail
3. Messages 阶段：通过 to_llm_message() 转换为 LLM 消息列表

将消息构建逻辑从 AgentLoop 中解耦，AgentLoop 每轮调用
ContextBuilder.build 获取消息列表，无需关心压缩和路径细节。
"""

from __future__ import annotations

from typing import Any

from pyagent.llm.token_estimator import ContextUsage
from pyagent.llm.types import Usage
from pyagent.session.messages import message_to_llm
from pyagent.session.types import Session
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


class ContextBuilder:
    """上下文构建器。

    将会话消息 + 系统提示词 + 当前用户输入组装为 LLM 消息列表。
    支持压缩感知：压缩摘要消息自动以 system 角色注入。

    Args:
        system_prompt: 系统提示词。
        context_usage: 上下文使用量监控器（可选）。每次build()后会自动update()，可用于阈值检测和压缩触发。
    """

    def __init__(
        self,
        system_prompt: str = "",
        context_usage: ContextUsage | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._context_usage = context_usage

    @property
    def system_prompt(self) -> str:
        """系统提示词。"""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """设置系统提示词"""
        self._system_prompt = value

    @property
    def context_usage(self) -> ContextUsage | None:
        """上下文使用量监控器（只读引用）。"""
        return self._context_usage

    def build(
        self,
        session: Session,
        last_usage: Usage | None = None,
    ) -> list[dict[str, Any]]:
        """构建 LLM 消息列表。

        1. 系统提示词 → messages[0]
        2. 会话历史（通过 message_to_llm 投影，跳过普通 system 消息，
           但保留压缩摘要等 type != "system" 的 system 角色消息）

        Args:
            session: 会话对象
            last_usage: 最近一次 LLM 调用的 usage（用于精确 token 估算，会传给 ContextUsage.update）。

        Returns:
            LLM 格式的消息列表。
        """
        messages: list[dict[str, Any]] = []

        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})

        # 历史消息（通过 message_to_llm() 投影）
        for msg in session.messages:
            llm_msg = message_to_llm(msg)
            # 跳过普通 system 消息(已由系统提示词重建), 但保留压缩摘要等特殊 system 消息（type != "system"）
            if llm_msg.get("role") == "system":
                msg_type = getattr(msg, "type", None)
                if msg_type is None or msg_type == "system":
                    continue
            messages.append(llm_msg)

        # 更新上下文使用量（精确值优先，否则回退估算）
        if self._context_usage is not None:
            self._context_usage.update(messages, last_usage)

        return messages

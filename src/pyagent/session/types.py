"""Session 数据模型。

Session 保存一次完整对话的上下文：
- 元数据（ID、创建时间、模型、token 统计、上下文窗口配置）
- 消息列表（支持 AgentMessage 丰富类型 + 向后兼容 Message）

借鉴 Pi Agent 的会话设计，增加：
- context_window: 模型上下文窗口大小
- compaction_threshold: 压缩触发阈值
- last_compaction_at: 上次压缩时间
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pyagent.session.messages import AgentMessage, message_to_llm


class SessionMetadata(BaseModel):
    """会话元数据。"""

    id: str = Field(description="会话唯一 ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str = ""
    system_prompt: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turn_count: int = 0
    title: str = ""

    # 模型上下文窗口大小（token 数），用于压缩触发判断
    context_window: int = 0
    # 压缩触发阈值（0~1），达到 context_window * threshold 时触发
    compaction_threshold: float = 0.8
    # 上次压缩的时间戳
    last_compaction_at: datetime | None = None


class Session(BaseModel):
    """完整会话。

    Attributes:
        metadata: 元数据。
        messages: 消息列表（判别联合 AgentMessage，支持所有子类型）。
    """

    metadata: SessionMetadata
    messages: list[AgentMessage] = Field(default_factory=list)

    def add_message(self, message: AgentMessage) -> None:
        """添加消息并更新时间戳。
        Args:
            message: 消息对象，支持 AgentMessage 子类型。
        """
        self.messages.append(message)
        self.metadata.updated_at = datetime.now(UTC)

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        """累加 token 用量。"""
        self.metadata.total_input_tokens += input_tokens
        self.metadata.total_output_tokens += output_tokens

    def increment_turn(self) -> None:
        """增加轮次计数。"""
        self.metadata.turn_count += 1

    def to_messages(self) -> list[dict[str, Any]]:
        """转换为 LLM API 格式的消息列表。

        通过 ``message_to_llm()`` 统一投影函数处理所有消息类型，
        支持 AgentMessage 子类型（含压缩摘要等）和 Message。
        """
        return [message_to_llm(msg) for msg in self.messages]

    def to_file(self, path: Path) -> None:
        """保存到 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def from_file(cls, path: Path) -> Session:
        """从 JSON 文件加载。"""
        data = path.read_text(encoding="utf-8")
        return cls.model_validate_json(data)

    @classmethod
    def create_new(
        cls,
        session_id: str,
        model: str = "",
        system_prompt: str = "",
        title: str = "",
        context_window: int = 0,
        compaction_threshold: float = 0.8,
    ) -> Session:
        """创建新会话。

        Args:
            session_id: 会话唯一 ID。
            model: 使用的模型名。
            system_prompt: 系统提示词。
            title: 会话标题。
            context_window: 模型上下文窗口大小（0 表示未设置）。
            compaction_threshold: 压缩触发阈值（0~1）。
        """
        metadata = SessionMetadata(
            id=session_id,
            model=model,
            system_prompt=system_prompt,
            title=title,
            context_window=context_window,
            compaction_threshold=compaction_threshold,
        )
        return cls(metadata=metadata)

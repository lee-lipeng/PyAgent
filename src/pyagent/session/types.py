"""Session 数据模型。

Session 保存一次完整对话的上下文：
- 元数据（ID、创建时间、模型、token 统计、上下文窗口配置）
- 消息列表（支持 AgentMessage 丰富类型 + 向后兼容 Message）

借鉴 Pi Agent 的会话设计，增加：
- context_window: 模型上下文窗口大小
- compaction_threshold: 压缩触发阈值
- last_compaction_at: 上次压缩时间
- mode: persistent / ephemeral。ephemeral 模式不写盘、单次任务用完即弃
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

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
        mode: 持久化模式。

            - persistent: 正常持久化到磁盘（默认）。
            - ephemeral: 仅在内存中存在，单次任务用完即弃。
              不会触发任何文件 I/O。供 CLI 一次性问答、SDK fire-and-forget
              等场景使用，避免污染用户会话目录。
    """

    metadata: SessionMetadata
    messages: list[AgentMessage] = Field(default_factory=list)
    mode: Literal["persistent", "ephemeral"] = "persistent"

    def add_message(self, message: AgentMessage) -> None:
        """添加消息。

        Args:
            message: 消息对象，支持 AgentMessage 子类型。
        """
        self.messages.append(message)

    def touch(self) -> None:
        """刷新updated_at为当前时间。

        通常在 to_file() 之前自动调用，外部不需手动触发。
        """
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
        """保存到 JSON 文件。

        保存前自动touch()一次，确保文件中的updated_at是当前最新值。
        """
        self.touch()
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
        mode: Literal["persistent", "ephemeral"] = "persistent",
    ) -> Session:
        """创建新会话。

        Args:
            session_id: 会话唯一 ID。
            model: 使用的模型名。
            system_prompt: 系统提示词。
            title: 会话标题。
            context_window: 模型上下文窗口大小（0 表示未设置）。
            compaction_threshold: 压缩触发阈值（0~1）。
            mode: 持久化模式（默认persistent）。
                ephemeral 表示不写盘、单次任务用完即弃。
        """
        metadata = SessionMetadata(
            id=session_id,
            model=model,
            system_prompt=system_prompt,
            title=title,
            context_window=context_window,
            compaction_threshold=compaction_threshold,
        )
        return cls(metadata=metadata, mode=mode)

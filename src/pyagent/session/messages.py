"""AgentMessage 类型体系。

借鉴 Pi Agent 的 7 种 AgentMessage 设计，将消息从单一的 ``Message``
拆分为有语义的子类型，每种类型知道如何将自己投影为 LLM 标准格式。

消息类型：
    - UserMessage: 用户输入
    - AssistantMessage: LLM 回复（含 tool_calls）
    - ToolResultMessage: 工具执行结果
    - CompactionSummaryMessage: 上下文压缩摘要
    - BranchSummaryMessage: 分支摘要
    - CustomMessage: 扩展自定义消息
    - Message: 通用消息类型（role/content/tool_calls 等）

每种消息实现 ``to_llm_message()`` 方法，
将语义消息投影为 LLM API 标准的 ``{role, content, ...}`` 字典。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class UserMessage(BaseModel):
    """用户输入消息。"""

    type: Literal["user"] = "user"
    content: str

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。"""
        return {"role": "user", "content": self.content}


class AssistantMessage(BaseModel):
    """LLM 回复消息。

    可能包含文本内容和/或工具调用请求。
    """

    type: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。

        无 tool_calls 时省略该字段，避免发送空列表。
        """
        msg: dict[str, Any] = {"role": "assistant"}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


class ToolResultMessage(BaseModel):
    """工具执行结果消息。"""

    type: Literal["tool"] = "tool"
    content: str
    tool_call_id: str
    name: str

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。"""
        return {
            "role": "tool",
            "content": self.content,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }


class CompactionSummaryMessage(BaseModel):
    """上下文压缩摘要消息。

    当上下文过长时，将历史消息压缩为结构化摘要，
    以 system 角色注入，让 LLM 理解之前的对话要点。

    摘要格式为结构化 Markdown，包含：
    - 目标 / 关键决策 / 文件变更 / 待办 / 上下文笔记
    """

    type: Literal["compaction_summary"] = "compaction_summary"
    content: str
    #: 被压缩的消息范围（起始 ~ 结束的 entry ID）
    compacted_range: str = ""
    #: 生成摘要时的 token 数
    summary_tokens: int = 0

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。

        压缩摘要以 system 角色注入，让 LLM 理解为上下文背景。
        """
        return {
            "role": "system",
            "content": f"[上下文压缩摘要]\n\n{self.content}",
        }


class BranchSummaryMessage(BaseModel):
    """分支摘要消息。

    从对话树的某个分支生成摘要，帮助 LLM 理解其他分支的探索内容。
    """

    type: Literal["branch_summary"] = "branch_summary"
    content: str
    #: 摘要来源的分支 ID
    branch_id: str = ""

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。

        分支摘要以 system 角色注入。
        """
        return {
            "role": "system",
            "content": f"[分支摘要]\n\n{self.content}",
        }


class CustomMessage(BaseModel):
    """自定义扩展消息。

    用于插件或用户自定义的消息类型，不归入标准角色体系。
    可通过 ``role`` 指定投影后的 LLM 角色。
    """

    type: Literal["custom"] = "custom"
    content: str
    #: 投影到 LLM 时的角色（system / user / assistant）
    role: str = "system"
    #: 自定义元数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。"""
        return {"role": self.role, "content": self.content}


class Message(BaseModel):
    """通用消息类型。

    Attributes:
        role: 角色（system/user/assistant/tool）。
        content: 文本内容。
        tool_calls: assistant 消息中的工具调用列表。
        tool_call_id: tool 消息对应的工具调用 ID。
        name: tool 消息对应的工具名。
    """

    type: Literal["message"] = "message"
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_llm_message(self) -> dict[str, Any]:
        """投影为 LLM 格式。"""
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg


AgentMessage = Annotated[
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | CompactionSummaryMessage
    | BranchSummaryMessage
    | CustomMessage
    | Message,
    Field(discriminator="type"),
]
"""所有 Agent 消息类型的判别联合（按 ``type`` 字段路由）。"""


def message_to_llm(msg: Any) -> dict[str, Any]:
    """将任意消息类型投影为 LLM 格式。

    统一的转换入口，支持 ``to_llm_message()`` 方法的所有消息类型。

    Args:
        msg: 消息对象（UserMessage / AssistantMessage 等）。

    Returns:
        LLM 格式的消息字典。
    """
    if hasattr(msg, "to_llm_message"):
        return msg.to_llm_message()

    # 兜底：直接 model_dump
    return msg.model_dump(exclude_none=True)

"""LLM 层基础类型。

这些类型是 litellm 原始返回的精简版，
屏蔽不同 Provider 的字段差异，给上层提供统一接口。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """LLM 返回的工具调用请求。

    Attributes:
        id: 调用唯一 ID（同一工具调用的流式片段共享同一 id）。
        name: 工具名。
        arguments: 已解析的参数字典。
        raw_arguments: 原始 JSON 字符串片段（流式过程中逐步拼接）。
    """

    id: str
    name: str
    arguments: dict = Field(default_factory=dict)
    # 流式过程中累积的原始 JSON 字符串片段，聚合器在结束时统一解析
    raw_arguments: str = ""
    # 流式片段的索引，同一工具调用的所有片段共享同一 index
    index: int = 0


class Usage(BaseModel):
    """Token 用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """总 token 数（输入 + 输出）。"""
        return self.input_tokens + self.output_tokens


class StreamChunk(BaseModel):
    """流式响应的单个 chunk。

    - delta: 增量文本片段（可能为空字符串）
    - tool_calls: 工具调用的增量片段（流式过程中逐步累积）
    """

    delta: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None

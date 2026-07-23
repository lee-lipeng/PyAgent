"""LLM 完整响应模型。

一次 LLM 调用结束后聚合的完整结果，
包含文本内容、工具调用、token 用量等。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pyagent.llm.types import ToolCall, Usage


class LLMResponse(BaseModel):
    """LLM 一次调用的完整响应。"""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str = "stop"  # stop | tool_use | length | content_filter
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用。"""
        return len(self.tool_calls) > 0

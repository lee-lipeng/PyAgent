"""LLM 抽象层：litellm 薄封装。

litellm 已统一 100+ Provider 的调用差异，这里只做薄封装：
- 统一返回类型（LLMResponse）
- 统一流式接口（AsyncIterator[StreamChunk]）
- 统一工具调用格式（ToolCall
- 统一 usage 统计（Usage）
- 统一 token 估算（estimate_tokens、estimate_messages_tokens、estimate_session_context）
"""

from pyagent.llm.client import LLMClient
from pyagent.llm.response import LLMResponse
from pyagent.llm.token_estimator import (
    ContextUsage,
    build_session_usage,
    calculate_context_tokens,
    estimate_messages_tokens,
    estimate_session_context,
    estimate_tokens,
)
from pyagent.llm.types import StreamChunk, ToolCall, Usage

__all__ = [
    "ContextUsage",
    "LLMClient",
    "LLMResponse",
    "StreamChunk",
    "ToolCall",
    "Usage",
    "build_session_usage",
    "calculate_context_tokens",
    "estimate_messages_tokens",
    "estimate_session_context",
    "estimate_tokens",
]

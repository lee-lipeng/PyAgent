"""LLMClient — litellm 薄封装的统一调用入口。

核心方法：
- stream(): 流式调用，yield StreamChunk，结束后可获取完整 LLMResponse
- complete(): 非流式调用，直接返回 LLMResponse

litellm 的 acompletion() 已统一各 Provider 的差异，
这里只做返回类型归一化和错误包装。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from pyagent.llm.exceptions import LLMError, ProviderError
from pyagent.llm.response import LLMResponse
from pyagent.llm.streaming import StreamAggregator
from pyagent.llm.types import StreamChunk, ToolCall, Usage
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    """LLM 统一调用客户端。

    对 litellm 的 acompletion 做薄封装，
    统一流式接口和返回类型。

    Args:
        model: litellm 格式的模型名，如 "openai/gpt-4o"、"anthropic/claude-sonnet-4-6"。
        api_key: API Key，为 None 时由 litellm 从环境变量读取。
        base_url: 自定义 API 地址（兼容 OpenAI 格式的代理）。
        timeout: 请求超时秒数。
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用 LLM，yield StreamChunk。

        Args:
            messages: litellm 格式的消息列表。
            tools: 工具 schema 列表（OpenAI function calling 格式）。
            **kwargs: 透传给 litellm.acompletion 的额外参数。

        Yields:
            StreamChunk: 流式 chunk，包含增量文本和工具调用片段。

        Raises:
            LLMError: 调用失败时抛出。
        """
        try:
            from litellm import acompletion
        except ImportError as exc:
            raise LLMError("litellm 未安装，请执行 uv add litellm") from exc

        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "timeout": self.timeout,
            "stream_options": {"include_usage": True},
        }
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["api_base"] = self.base_url
        if tools:
            call_kwargs["tools"] = tools
        call_kwargs.update(kwargs)

        try:
            response = await acompletion(**call_kwargs)
        except Exception as exc:
            logger.error("LLM 调用失败: %s", exc)
            raise ProviderError(f"LLM 调用失败: {exc}") from exc
        async for chunk in response:
            parsed = self._parse_chunk(chunk)
            if parsed is not None:
                yield parsed

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """非流式调用 LLM，直接返回完整响应。

        内部仍用 stream() 实现，聚合所有 chunk 后返回。
        这样只需维护一条代码路径。
        """
        agg = StreamAggregator(model=self.model)
        async for chunk in self.stream(messages, tools, **kwargs):
            agg.feed(chunk)
        return agg.result()

    async def stream_and_collect(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[AsyncIterator[StreamChunk], StreamAggregator]:
        """流式调用并返回聚合器，调用方可边透传边收集最终结果。

        用法::

            chunks, agg = await client.stream_and_collect(messages, tools)
            async for chunk in chunks:
                print(chunk.delta, end="")
            response = agg.result()
        """
        agg = StreamAggregator(model=self.model)

        async def _pipe() -> AsyncIterator[StreamChunk]:
            async for chunk in self.stream(messages, tools, **kwargs):
                agg.feed(chunk)
                yield chunk

        return _pipe(), agg

    def _parse_chunk(self, chunk: Any) -> StreamChunk | None:
        """把 litellm 的原始 chunk 解析为 StreamChunk。

        litellm 的 chunk 格式基本兼容 OpenAI，
        但不同 Provider 可能有细微差异，这里统一处理。
        """
        try:
            usage = None
            raw_usage = getattr(chunk, "usage", None)
            if raw_usage:
                usage = Usage(
                    input_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
                )

            if not chunk.choices:
                return StreamChunk(usage=usage)

            choice = chunk.choices[0]
            delta = choice.delta

            # 文本增量
            text_delta = getattr(delta, "content", None) or ""

            # 工具调用增量
            tool_calls: list[ToolCall] = []
            raw_tool_calls = getattr(delta, "tool_calls", None)
            if raw_tool_calls:
                for rtc in raw_tool_calls:
                    tc_id = getattr(rtc, "id", None) or ""
                    tc_index = getattr(rtc, "index", None)
                    if tc_index is None:
                        tc_index = 0
                    function = getattr(rtc, "function", None)
                    tc_name = getattr(function, "name", None) or "" if function else ""
                    raw_args = getattr(function, "arguments", None) if function else None
                    # arguments 在流式中是 JSON 字符串片段，逐片拼接后才能解析
                    # 这里保留原始字符串，完整解析交给 StreamAggregator
                    raw_str = ""
                    arguments: dict = {}
                    if raw_args and isinstance(raw_args, str):
                        raw_str = raw_args
                        # 尝试解析（非流式时 arguments 是完整 JSON）
                        with contextlib.suppress(json.JSONDecodeError):
                            parsed = json.loads(raw_args) if raw_args.strip() else {}
                            arguments = parsed if isinstance(parsed, dict) else {}
                    elif raw_args and isinstance(raw_args, dict):
                        arguments = raw_args

                    tool_calls.append(
                        ToolCall(
                            id=tc_id,
                            name=tc_name,
                            arguments=arguments,
                            raw_arguments=raw_str,
                            index=tc_index,
                        )
                    )

            finish_reason = getattr(choice, "finish_reason", None)

            return StreamChunk(
                delta=text_delta,
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=finish_reason,
            )
        except (AttributeError, IndexError, TypeError) as exc:
            logger.debug("跳过无法解析的 chunk: %s", exc)
            return None

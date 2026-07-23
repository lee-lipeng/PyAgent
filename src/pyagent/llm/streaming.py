"""流式响应聚合器。

litellm 的流式接口返回一系列 chunk，需要手动聚合：
- 文本 delta 拼接成完整 content
- 工具调用 delta 按 index 累积成完整 ToolCall
- 最后一个 chunk 的 usage 和 finish_reason 作为最终值
"""

from __future__ import annotations

import json
from typing import Any

from pyagent.llm.response import LLMResponse
from pyagent.llm.types import StreamChunk, ToolCall, Usage
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


class StreamAggregator:
    """流式 chunk 聚合器。

    用法::

        agg = StreamAggregator(model="gpt-4o")
        async for chunk in stream:
            chunk_t = parse_chunk(chunk)
            agg.feed(chunk_t)
            yield chunk_t  # 同时透传给上层
        response = agg.result()
    """
    def __init__(self, model: str = "") -> None:
        self._model = model
        self._content_parts: list[str] = []
        # key → {id, name, raw_parts} 用于累积工具调用
        # raw_parts 是 JSON 字符串片段列表，最终拼接后统一解析
        self._tool_call_builders: dict[str, dict[str, Any]] = {}
        self._usage: Usage | None = None
        self._finish_reason: str | None = None

    def feed(self, chunk: StreamChunk) -> None:
        """喂入一个 chunk，累积内容。"""
        if chunk.delta:
            self._content_parts.append(chunk.delta)
        if chunk.tool_calls:
            for tc in chunk.tool_calls:
                # 用 index 作为 key（OpenAI 流式格式中同一工具调用的
                # 所有片段共享同一 index，id 只在第一个片段出现）
                key = str(tc.index)
                builder = self._tool_call_builders.setdefault(key, {"id": "", "name": "", "raw_parts": []})
                # id 只在第一个片段出现，后续片段 id 为空
                if tc.id:
                    builder["id"] = tc.id
                if tc.name:
                    builder["name"] = tc.name
                # 累积原始 JSON 字符串片段
                if tc.raw_arguments:
                    builder["raw_parts"].append(tc.raw_arguments)
                # 非流式场景：arguments 已经是完整 dict，直接存
                if tc.arguments and isinstance(tc.arguments, dict) and not builder["raw_parts"]:
                    builder.setdefault("arguments", {})
                    builder["arguments"].update(tc.arguments)
        if chunk.usage:
            self._usage = chunk.usage
        if chunk.finish_reason:
            self._finish_reason = chunk.finish_reason

    def result(self) -> LLMResponse:
        """返回聚合后的完整响应。"""
        tool_calls: list[ToolCall] = []
        for b in self._tool_call_builders.values():
            # 拼接所有原始 JSON 片段，统一解析
            raw = "".join(b.get("raw_parts", []))
            arguments: dict = {}
            if raw:
                try:
                    parsed = json.loads(raw)
                    arguments = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    logger.warning("工具调用参数 JSON 解析失败: %s", raw[:200])
            elif "arguments" in b:
                arguments = b["arguments"]
            # 兜底：某些 provider 可能不返回 id，生成唯一 id 避免重复
            tc_id = b["id"] or f"call_{len(tool_calls)}"
            tool_calls.append(ToolCall(id=tc_id, name=b["name"], arguments=arguments))
        return LLMResponse(
            content="".join(self._content_parts),
            tool_calls=tool_calls,
            usage=self._usage or Usage(),
            stop_reason=self._finish_reason or "stop",
            model=self._model,
        )

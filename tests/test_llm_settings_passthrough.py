"""LLM 客户端必须透传已配置的温度和最大输出 Token 数。"""

import sys
from types import SimpleNamespace

import pytest

from pyagent.llm.client import LLMClient


@pytest.mark.asyncio
async def test_llm_settings_and_request_overrides(monkeypatch):
    captured = {}

    async def completion(**kwargs):
        captured.update(kwargs)

        async def chunks():
            yield SimpleNamespace(choices=[])

        return chunks()

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=completion))
    client = LLMClient("openai/test", temperature=0.35, max_tokens=321)

    await client.complete([{"role": "user", "content": "hi"}])
    assert captured["temperature"] == 0.35
    assert captured["max_tokens"] == 321

    captured.clear()
    await client.complete([{"role": "user", "content": "hi"}], temperature=0.1)
    assert captured["temperature"] == 0.1
    assert captured["max_tokens"] == 321

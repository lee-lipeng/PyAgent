"""LLM 流式生成期间 Abort 必须即时生效，不依赖下一个 Chunk。"""

import asyncio
from types import SimpleNamespace

import pytest

from pyagent.core.context import RuntimeContext
from pyagent.core.loop import AgentLoop
from pyagent.hooks.types import DispatchResult, EventType
from pyagent.llm.response import LLMResponse
from pyagent.llm.types import StreamChunk
from pyagent.session.types import Session


class BlockingLLM:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream_and_collect(self, **kwargs):
        async def chunks():
            try:
                self.started.set()
                yield StreamChunk(delta="开始响应")
                await asyncio.Event().wait()
            finally:
                self.closed.set()

        return chunks(), SimpleNamespace(
            result=lambda: LLMResponse(content="不应执行到此")
        )


class FastLLM:
    async def stream_and_collect(self, **kwargs):
        async def chunks():
            yield StreamChunk(delta="正常输出")

        return chunks(), SimpleNamespace(
            result=lambda: LLMResponse(content="正常结束")
        )


class FakeAgent:
    max_turns = 2

    def __init__(self, llm):
        self.llm_client = llm
        self.events = []

    def build_system_prompt(self, base_prompt=None):
        return "test"

    def get_tool_schemas(self):
        return []

    async def dispatch(self, event_type, initial=None, **kwargs):
        self.events.append(event_type)
        return DispatchResult(value=initial)


@pytest.mark.asyncio
async def test_abort_during_blocked_stream_returns_without_next_chunk():
    session = Session.create_new("stream-cancel")
    llm = BlockingLLM()
    agent = FakeAgent(llm)
    ctx = RuntimeContext(query="开始", session=session)
    received = []

    task = asyncio.create_task(
        AgentLoop(agent).run("开始", session, on_chunk=received.append, ctx=ctx)
    )
    await asyncio.wait_for(llm.started.wait(), timeout=1)
    ctx.cancel()

    result = await asyncio.wait_for(task, timeout=1)

    assert not result.success
    assert result.stop_reason == "cancelled"
    assert llm.closed.is_set()
    assert received == ["开始响应"]
    assert EventType.AGENT_ABORT in agent.events
    assert [message.type for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_normal_stream_still_completes():
    session = Session.create_new("stream-ok")
    agent = FakeAgent(FastLLM())
    ctx = RuntimeContext(query="开始", session=session)
    received = []

    result = await AgentLoop(agent).run(
        "开始", session, on_chunk=received.append, ctx=ctx
    )

    assert result.success
    assert result.final_response == "正常结束"
    assert received == ["正常输出"]
    assert [message.type for message in session.messages] == ["user", "assistant"]

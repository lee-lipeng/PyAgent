"""LLM 完成事件、输出截断及工具异常时的会话一致性。"""

import pytest

from pyagent.core.loop import AgentLoop
from pyagent.hooks.types import DispatchResult, EventType
from pyagent.llm.response import LLMResponse
from pyagent.llm.types import ToolCall
from pyagent.session.types import Session


class StubLLM:
    def __init__(self, response):
        self.response = response

    async def stream_and_collect(self, **kwargs):
        async def chunks():
            if False:
                yield None

        response = self.response
        return chunks(), type("Collector", (), {"result": lambda self: response})()


class FailingExecutor:
    async def execute_batch(self, calls, signal=None):
        raise RuntimeError("batch failed")


class StubAgent:
    max_turns = 2

    def __init__(self, response, *, cancel_post=False):
        self.llm_client = StubLLM(response)
        self.tool_executor = FailingExecutor()
        self.cancel_post = cancel_post

    def build_system_prompt(self, base_prompt=None):
        return "test"

    def get_tool_schemas(self):
        return []

    async def dispatch(self, event_type, initial=None, **kwargs):
        if self.cancel_post and event_type == EventType.AFTER_LLM:
            return DispatchResult(cancelled=True, cancel_reason="审查失败", value=initial)
        return DispatchResult(value=initial)


@pytest.mark.asyncio
async def test_after_llm_cancellation_prevents_tool_dispatch():
    session = Session.create_new("post-cancel")
    agent = StubAgent(
        LLMResponse(tool_calls=[ToolCall(id="call-a", name="read", arguments={})]),
        cancel_post=True,
    )
    result = await AgentLoop(agent).run("hello", session)
    assert not result.success
    assert result.stop_reason == "llm_cancelled"
    assert result.error == "审查失败"
    assert [m.type for m in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_length_finish_reason_is_not_reported_as_complete():
    session = Session.create_new("cut")
    agent = StubAgent(LLMResponse(content="半段文本", stop_reason="length"))
    result = await AgentLoop(agent).run("hello", session)
    assert not result.success
    assert result.stop_reason == "output_truncated"
    assert result.final_response == "半段文本"


@pytest.mark.asyncio
async def test_tool_failure_still_persists_matched_tool_results():
    session = Session.create_new("failed-batch")
    agent = StubAgent(
        LLMResponse(tool_calls=[
            ToolCall(id="a", name="read", arguments={"path": "a"}),
            ToolCall(id="b", name="read", arguments={"path": "b"}),
        ])
    )
    result = await AgentLoop(agent).run("hello", session)
    assert not result.success
    assert result.stop_reason == "tool_error"
    assert [m.type for m in session.messages] == ["user", "assistant", "tool", "tool"]
    assert [m.tool_call_id for m in session.messages[2:]] == ["a", "b"]

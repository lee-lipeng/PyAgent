"""取消工具批次后保留可恢复的工具调用/结果配对。"""

from types import SimpleNamespace

import pytest

from pyagent.core.loop import AgentLoop
from pyagent.hooks.types import DispatchResult, EventType
from pyagent.llm.response import LLMResponse
from pyagent.llm.types import ToolCall
from pyagent.session.types import Session


class FakeLLM:
    async def stream_and_collect(self, **kwargs):
        async def chunks():
            if False:
                yield None

        response = LLMResponse(tool_calls=[ToolCall(id="tc-1", name="read", arguments={})])
        return chunks(), SimpleNamespace(result=lambda: response)


class FakeAgent:
    max_turns = 2
    llm_client = FakeLLM()

    def build_system_prompt(self, base_prompt=None):
        return base_prompt or "test"

    def get_tool_schemas(self):
        return []

    async def dispatch(self, event_type, initial=None, **payload):
        if event_type == EventType.TOOL_BATCH_START:
            return DispatchResult(cancelled=True, cancel_reason="已拒绝", value=initial)
        return DispatchResult(value=initial)


@pytest.mark.asyncio
async def test_cancelled_tool_batch_records_result():
    session = Session.create_new("cancel")
    result = await AgentLoop(FakeAgent()).run("请读取文件", session)
    assert result.stop_reason == "tool_batch_cancelled"
    assert [m.type for m in session.messages] == ["user", "assistant", "tool"]
    assert session.messages[1].tool_calls[0]["id"] == session.messages[2].tool_call_id
    assert session.messages[2].content == "已拒绝"

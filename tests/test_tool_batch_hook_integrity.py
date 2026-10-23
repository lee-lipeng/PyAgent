"""Hook 修改工具批次时，调用 ID 和工具结果必须保持配对。"""

from types import SimpleNamespace

import pytest

from pyagent.core.loop import AgentLoop
from pyagent.hooks.types import DispatchResult, EventType
from pyagent.llm.response import LLMResponse
from pyagent.llm.types import ToolCall
from pyagent.session.types import Session
from pyagent.tools.base import ToolResult


class FakeLLM:
    def __init__(self):
        self.count = 0

    async def stream_and_collect(self, **kwargs):
        self.count += 1

        async def chunks():
            if False:
                yield None

        if self.count == 1:
            result = LLMResponse(
                tool_calls=[ToolCall(id="id-1", name="read", arguments={"path": "old"})]
            )
        else:
            result = LLMResponse(content="完成")
        return chunks(), SimpleNamespace(result=lambda: result)


class FakeExecutor:
    async def execute_batch(self, calls, signal=None):
        return [ToolResult(content="读取完成") for _ in calls]


class FakeAgent:
    max_turns = 3

    def __init__(self, mode):
        self.mode = mode
        self.llm_client = FakeLLM()
        self.tool_executor = FakeExecutor()

    def build_system_prompt(self, base_prompt=None):
        return base_prompt or "test"

    def get_tool_schemas(self):
        return []

    async def dispatch(self, event_type, initial=None, **kwargs):
        if event_type == EventType.TOOL_BATCH_START:
            if self.mode == "invalid-id":
                return DispatchResult(value=[ToolCall(id="other", name="read", arguments={})])
            if self.mode == "change-args":
                return DispatchResult(value=[
                    ToolCall(id="id-1", name="read", arguments={"path": "new"})
                ])
        if event_type == EventType.TOOL_BATCH_END and self.mode == "invalid-results":
            return DispatchResult(value=[])
        return DispatchResult(value=initial)


@pytest.mark.asyncio
async def test_invalid_batch_ids_rejected_and_paired():
    session = Session.create_new("bad")
    result = await AgentLoop(FakeAgent("invalid-id")).run("开始", session)
    assert result.stop_reason == "invalid_tool_batch"
    assert [m.type for m in session.messages] == ["user", "assistant", "tool"]
    assert session.messages[1].tool_calls[0]["id"] == session.messages[2].tool_call_id


@pytest.mark.asyncio
async def test_batch_args_rewrite_reflected_in_history():
    session = Session.create_new("modified")
    result = await AgentLoop(FakeAgent("change-args")).run("开始", session)
    assert result.success
    assert '"path": "new"' in session.messages[1].tool_calls[0]["function"]["arguments"]
    assert session.messages[2].tool_call_id == "id-1"


@pytest.mark.asyncio
async def test_invalid_batch_results_fallback_to_executor():
    session = Session.create_new("fallback")
    result = await AgentLoop(FakeAgent("invalid-results")).run("开始", session)
    assert result.success
    assert session.messages[2].content == "读取完成"


class ArgumentRewritingExecutor:
    async def execute_batch(self, calls, signal=None):
        # 模拟 BEFORE_TOOL Hook 执行期间原地改写 ToolCall 参数。
        calls[0].arguments = {"path": "effective.txt"}
        return [ToolResult(content="已处理")]


@pytest.mark.asyncio
async def test_before_tool_argument_rewrite_persisted_after_execution():
    session = Session.create_new("audited")
    agent = FakeAgent("normal")
    agent.tool_executor = ArgumentRewritingExecutor()

    result = await AgentLoop(agent).run("开始", session)

    assert result.success
    arguments = session.messages[1].tool_calls[0]["function"]["arguments"]
    assert '"path": "effective.txt"' in arguments
    assert session.messages[2].tool_call_id == "id-1"

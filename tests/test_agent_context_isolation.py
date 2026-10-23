"""同一个 AgentLoop 的不同 Session 不能复用上下文构建器。"""

from pyagent.core.loop import AgentLoop
from pyagent.session.types import Session


class FakeAgent:
    def build_system_prompt(self, base_prompt=None):
        return base_prompt or "默认提示词"


def test_context_builder_isolated_per_session():
    loop = AgentLoop(FakeAgent())
    first_session = Session.create_new("one", system_prompt="会话一", context_window=512)
    second_session = Session.create_new("two", system_prompt="会话二", context_window=1024)

    first = loop._ensure_context_builder(first_session)
    second = loop._ensure_context_builder(second_session)

    assert first is not second
    assert first.system_prompt == "会话一"
    assert second.system_prompt == "会话二"
    assert first.context_usage.limit == 512
    assert second.context_usage.limit == 1024
    assert loop.context_usage is second.context_usage

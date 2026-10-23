"""工具调用参数损坏时不得静默降级为空字典执行。"""

import pytest

from pyagent.llm.streaming import StreamAggregator
from pyagent.llm.types import StreamChunk, ToolCall


@pytest.mark.parametrize("raw", ['{"broken":', '["not", "object"]', 'null'])
def test_reject_malformed_tool_arguments(raw):
    aggregator = StreamAggregator()
    aggregator.feed(
        StreamChunk(tool_calls=[
            ToolCall(id="call_1", name="run_bash", raw_arguments=raw)
        ])
    )
    with pytest.raises(ValueError, match="调用参数"):
        aggregator.result()


def test_valid_tool_arguments_still_parse():
    aggregator = StreamAggregator()
    aggregator.feed(
        StreamChunk(tool_calls=[
            ToolCall(id="call_1", name="read_file", raw_arguments='{"path":"README.md"}')
        ])
    )
    result = aggregator.result()
    assert result.tool_calls[0].arguments == {"path": "README.md"}

"""压缩切割点不得破坏并行工具调用结果的完整性。"""

from pyagent.core.compaction import CompactionManager


def _messages():
    return [
        {"role": "user", "content": "开始"},
        {"role": "assistant", "tool_calls": [
            {"id": "a", "function": {"name": "read", "arguments": "{}"}},
            {"id": "b", "function": {"name": "grep", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "name": "read", "content": "内容"},
        {"role": "tool", "tool_call_id": "b", "name": "grep", "content": "结果"},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "完成"},
    ]


def test_cut_does_not_split_parallel_tool_results():
    messages = _messages()
    manager = CompactionManager(retained_tail=3)
    assert manager.find_cut_point(messages) == 1
    assert not manager._is_safe_cut(messages, 3)
    assert manager._is_safe_cut(messages, 4)


def test_compaction_prompt_includes_tool_call_metadata():
    rendered = CompactionManager._render_user_prompt(_messages(), "")
    assert "read({})" in rendered
    assert "grep({})" in rendered
    assert "[tool:read] 内容" in rendered


def test_tool_output_is_bounded_for_summary():
    rendered = CompactionManager._render_user_prompt(
        [{"role": "tool", "name": "bash", "content": "x" * 10000}], ""
    )
    assert "工具输出已截断" in rendered
    assert len(rendered) < 2500

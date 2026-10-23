"""文件编辑失败时，不得破坏已存在的数据。"""

import pytest

from pyagent.tools.builtin.edit import EditFileTool
from pyagent.tools.builtin.write import WriteFileTool


@pytest.mark.asyncio
async def test_write_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "old.txt"
    target.write_text("old", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr("pyagent.tools.builtin.write.os.replace", fail_replace)
    result = await WriteFileTool().execute(
        "id", {"path": str(target), "content": "new"}
    )
    assert result.is_error
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["old.txt"]


@pytest.mark.asyncio
async def test_edit_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "old.txt"
    target.write_text("before", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr("pyagent.tools.builtin.write.os.replace", fail_replace)
    result = await EditFileTool().execute(
        "id", {"path": str(target), "old_string": "before", "new_string": "after"}
    )
    assert result.is_error
    assert target.read_text(encoding="utf-8") == "before"
    assert [p.name for p in tmp_path.iterdir()] == ["old.txt"]


@pytest.mark.asyncio
async def test_atomic_successful_write_and_edit(tmp_path):
    target = tmp_path / "new.txt"
    written = await WriteFileTool().execute(
        "id", {"path": str(target), "content": "old"}
    )
    edited = await EditFileTool().execute(
        "id", {"path": str(target), "old_string": "old", "new_string": "new"}
    )
    assert not written.is_error and not edited.is_error
    assert target.read_text(encoding="utf-8") == "new"

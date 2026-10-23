"""同名文件不能因 importlib 模块缓存而混用。"""

import pytest

from pyagent.tools.discovery import ToolDiscovery
from pyagent.tools.registry import ToolRegistry
from pyagent.utils.discovery import DiscoveryItem
from pyagent.utils.loader import import_module_from_path


def test_same_filename_in_different_directories(tmp_path):
    root = tmp_path
    for folder, name in [("one", "first_tool"), ("two", "second_tool")]:
        directory = root / folder
        directory.mkdir()
        (directory / "plugin.py").write_text(
            "from pyagent.tools.base import Tool, ToolResult\n"
            "from pyagent.tools.decorators import tool\n"
            f"@tool('{name}', description='test')\n"
            "class Plugin(Tool):\n"
            "    async def execute(self, tool_call_id, args, signal=None, on_update=None):\n"
            "        return ToolResult(content='ok')\n",
            encoding="utf-8",
        )

    registry = ToolRegistry()
    loader = ToolDiscovery(registry)
    for folder in ("one", "two"):
        path = root / folder / "plugin.py"
        assert loader.load(DiscoveryItem(path=path, name="plugin", source="user")).loaded
    assert set(registry.names()) == {"first_tool", "second_tool"}


def test_import_error_removes_half_loaded_module(tmp_path):
    import sys

    path = tmp_path / "broken.py"
    path.write_text("raise RuntimeError('broken')", encoding="utf-8")
    try:
        import_module_from_path(path, "test_review_failed_import")
    except ImportError:
        pass
    else:
        pytest.fail("expected ImportError")
    assert "test_review_failed_import" not in sys.modules

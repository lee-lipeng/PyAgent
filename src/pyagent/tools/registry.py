"""ToolRegistry — 工具注册表。

管理工具名→实例映射，支持注册、查询、列出所有工具 schema。
"""

from __future__ import annotations

from typing import Any

from pyagent.tools.base import Tool
from pyagent.tools.exceptions import ToolNotFoundError
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:
    """工具注册表。

    用法::

        registry = ToolRegistry()
        registry.register(ReadFileTool())
        tool = registry.get("read_file")  # ToolNotFoundError if missing
        schemas = registry.get_schemas()  # OpenAI function calling 格式
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具。同名工具会被覆盖。"""
        if not tool.name:
            raise ValueError(f"工具 {tool.__class__.__name__} 未定义 name 属性")
        if tool.name in self._tools:
            logger.debug(f"覆盖同名工具: {tool.name}")

        self._tools[tool.name] = tool
        logger.debug(f"注册工具: {tool.name}")

    def unregister(self, name: str) -> None:
        """移除已注册的工具。"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        """获取工具实例，不存在时抛 ToolNotFoundError。"""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        return tool

    def has(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    def all(self) -> list[Tool]:
        """返回所有已注册的工具列表。"""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """返回所有已注册的工具名列表。"""
        return list(self._tools.keys())

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的function calling schema 列表。"""
        return [tool.get_schema() for tool in self._tools.values()]

    def clear(self) -> None:
        """清空注册表。"""
        self._tools.clear()

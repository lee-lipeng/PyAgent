"""Tool 层异常。"""

from __future__ import annotations


class ToolError(Exception):
    """工具相关错误的基类。"""


class ToolNotFoundError(ToolError):
    """请求的工具不存在。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"工具不存在: {tool_name}")
        self.tool_name = tool_name


class ToolExecutionError(ToolError):
    """工具执行过程中出错。"""


class ToolValidationError(ToolError):
    """工具参数校验失败。"""

"""@tool 装饰器。

用于以声明式方式标记工具类，配合 ToolDiscovery 自动扫描注册。

用法::

    @tool("read_file", description="读取文件内容")
    class ReadFileTool(Tool):
        parameters_model = ReadFileArgs

        async def execute(self, tool_call_id, args, signal, on_update):
            ...

    # 或不传参数，用类名自动生成 name
    @tool
    class ReadFileTool(Tool):
        ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def tool(
    cls_or_name: type[T] | str | None = None,
    *,
    description: str = "",
    execution_mode: str = "parallel",
) -> Callable[..., Any]:
    """装饰器：标记一个类为工具。

    支持两种用法：
        @tool                          # 无参数，用类属性 name
        @tool("read_file")             # 指定 name
        @tool("read_file", description="读取文件")

    装饰后会在类上设置 _is_tool = True 标记，
    ToolDiscovery 扫描时会识别这个标记。
    """

    def _decorate(cls: type[T], name: str | None = None, desc: str = "") -> type[T]:
        cls._is_tool = True  
        if name:
            cls.name = name  
        if desc:
            cls.description = desc  
        cls.execution_mode = execution_mode  
        return cls

    # 用法 1: @tool（无括号，cls_or_name 是类）
    if isinstance(cls_or_name, type):
        return _decorate(cls_or_name)

    # 用法 2: @tool("name") 或 @tool(name="...", description="...")
    name = cls_or_name if isinstance(cls_or_name, str) else None
    return lambda cls: _decorate(cls, name=name, desc=description)

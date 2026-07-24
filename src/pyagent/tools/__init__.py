"""Tool 系统：基类、注册表、执行器、自动发现、装饰器。

设计要点：
- @tool 装饰器标记工具类，零配置自动注册
- ToolRegistry 管理工具名→实例映射
- ToolExecutor 负责执行，执行前后 emit 事件到 HookManager
- ToolDiscovery 扫描目录，自动加载所有 @tool 装饰的类
"""

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.executor import ToolExecutor
from pyagent.tools.registry import ToolRegistry

__all__ = ["Tool", "ToolResult", "ToolExecutor", "ToolRegistry"]

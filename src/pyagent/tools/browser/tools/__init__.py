"""browser_* 工具集合 — 由 ToolDiscovery 自动发现。

每个文件定义一个 @tool 工具类,落地 PyAgent 现有规范:
- Tool ABC 子类
- BaseModel 参数模型
- execution_mode = "sequential" (浏览器操作有状态,避免并发)
- 返回 ToolResult(content, is_error, details)

ToolDiscovery 会扫描本目录每个 *.py 自动发现 @tool类。
"""

from .execute_js import BrowserExecuteJsTool
from .install_hint import BrowserInstallHintTool
from .navigate import BrowserNavigateTool
from .scan import BrowserScanTool
from .screenshot import BrowserScreenshotTool
from .status import BrowserStatusTool

__all__ = [
    "BrowserStatusTool",
    "BrowserNavigateTool",
    "BrowserScanTool",
    "BrowserExecuteJsTool",
    "BrowserScreenshotTool",
    "BrowserInstallHintTool",
]

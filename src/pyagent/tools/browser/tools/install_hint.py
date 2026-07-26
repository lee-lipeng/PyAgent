"""browser_install_hint:返回浏览器桥安装指引。

LLM 收到 not_connected 错误时应调用一次此工具拿到完整指引,
而非反复重试原工具 (符合 user memory 错误时不要重复做什么原则)。
"""

from __future__ import annotations

from pydantic import BaseModel

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.browser import BrowserSettings, get_bridge
from pyagent.tools.decorators import tool


class BrowserInstallHintArgs(BaseModel):
    """无参数。保留以保持 schema 一致。"""


@tool(
    "browser_install_hint",
    description=(
        "返回浏览器桥安装指引 — 包括 Chrome 扩展加载步骤、WS 地址说明、"
        "常见问题排查。LLM 收到 not_connected 错误时应主动调用此工具。"
    ),
)
class BrowserInstallHintTool(Tool):
    """安装指引工具。"""

    parameters_model = BrowserInstallHintArgs
    execution_mode = "sequential"

    async def execute(
        self,
        tool_call_id: str,
        args: dict,
        signal=None,
        on_update=None,
    ) -> ToolResult:
        # 取当前桥的 settings (单例可能未初始化 → 默认值)
        bridge = get_bridge()
        settings = bridge.settings if bridge is not None else BrowserSettings()

        ws_url = settings.ws_url
        doc_url = settings.install_doc_url
        ws_port = settings.ws_port
        http_port = settings.effective_http_port

        lines = [
            "# 浏览器桥安装指引",
            "",
            "## 1. 加载 Chrome 扩展",
            "",
            "1. 打开 Chrome,访问 `chrome://extensions/`",
            "2. 打开右上角「开发者模式」开关",
            "3. 点击「加载未打包的扩展程序」,选择目录:",
            "   `<仓库根>/src/pyagent/tools/browser/extension/`",
            "4. 加载完成后扩展会自动连接本地 WS 服务",
            "",
            "## 2. 验证连接",
            "",
            "加载扩展后,在 PyAgent 中调用 `browser_status` 应返回「已连接」。",
            f"WS 地址: `{ws_url}`",
            f"HTTP 端口 (反向代理): {http_port}",
            "",
            "## 3. 端口冲突",
            "",
            f"默认 WS 端口 `{ws_port}` 被占用时,在 `settings.json` 中修改:",
            "```json",
            "{",
            '  "browser": { "ws_port": 19787, "http_port": 19788 }',
            "}",
            "```",
            "或环境变量:`PYAGENT_BROWSER__WS_PORT=19787`",
            "",
            "## 4. 常见问题",
            "",
            "- **扩展已加载但仍 not_connected**: 检查 Chrome 是否完全退出过,",
            "  部分场景需要重启 Chrome 才会重新连接 WS",
            "- **WS 端口不一致**: 扩展源码中的 WS 端口必须与 settings.json 的 ws_port 一致",
            "- **远程模式**: 如果另一进程已占用 http_port,本机会自动走反向代理模式",
            "- **彻底禁用**: `browser.enabled=false` 或 `PYAGENT_BROWSER__ENABLED=false`",
            "",
            f"## 详细文档: {doc_url}",
        ]

        return ToolResult(
            content="\n".join(lines),
            details={
                "ws_url": ws_url,
                "ws_port": ws_port,
                "http_port": http_port,
                "doc_url": doc_url,
                "enabled": settings.enabled,
            },
        )

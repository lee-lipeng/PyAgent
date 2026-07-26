"""浏览器桥配置。

可通过环境变量 / JSON 配置文件覆盖。
"""

from __future__ import annotations

from pydantic import BaseModel


class BrowserSettings(BaseModel):
    """浏览器桥配置。

    字段说明:
    - enabled: 总开关 (False 时所有 browser_* 工具返回 not_connected)
    - ws_host / ws_port: 本地 WS 服务器地址 (Chrome 扩展连接的目标)
    - http_port: 用于反向代理 / long-poll 的 HTTP 端口;
                     None 时自动用 ws_port + 1
    - auto_connect: Runtime.setup() 时是否自动建立连接
    - default_timeout: 工具调用默认超时 (秒)
    - scan_default_max_chars / scan_max_chars: browser_scan 默认 / 上限字符数
    - screenshot_max_bytes: 截图 base64 字符串最大字节数 (防撑爆 LLM 上下文)
    - network_capture_timeout: execute_js(monitor=network/full) 单次最大超时 (秒)
    - install_doc_url: browser_install_hint 工具返回的文档地址
    """

    enabled: bool = True
    ws_host: str = "127.0.0.1"
    ws_port: int = 18787
    http_port: int | None = None

    auto_connect: bool = True
    default_timeout: float = 15.0

    # 浏览器自动启动 — 当扩展 bg.js 连不上(Chrome 没开)时,
    # browser_status / browser_navigate 可自动拉起 Chrome。
    auto_launch_chrome: bool = True
    # Chrome 二进制路径;None 时由 chrome_launcher 自动检测
    chrome_path: str | None = None

    # browser_scan 默认 / 上限
    scan_default_max_chars: int = 20_000
    scan_max_chars: int = 100_000

    # browser_screenshot 截图体积上限 (base64 字节)
    # 200 KB 对应 ~150 KB PNG,LLM 上下文可控;超出会降采样 + 提示
    screenshot_max_bytes: int = 200_000

    # execute_js(monitor=network/full) 单次执行的最大超时 (秒)。
    # API 监控期间跑用户代码 + 触发 fetch + 捕获响应,容易撞到通用 default_timeout=15s,
    network_capture_timeout: float = 60.0

    install_doc_url: str = "https://github.com/lee-lipeng/PyAgent/tree/main/src/pyagent/tools/browser"

    @property
    def effective_http_port(self) -> int:
        """实际 HTTP 端口 (auto-fill 逻辑)。"""
        return self.http_port if self.http_port is not None else self.ws_port + 1

    @property
    def ws_url(self) -> str:
        """完整 WS URL。"""
        return f"ws://{self.ws_host}:{self.ws_port}"

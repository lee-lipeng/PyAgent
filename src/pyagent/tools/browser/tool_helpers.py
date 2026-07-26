"""浏览器工具共享 helper。

提供:
- get_settings():获取当前桥 settings
- not_connected_result():统一的"桥未连接"错误响应
- async_ensure_bridge():异步等到 bridge server ready,返回 (bridge, None) 或 (None, error)
- translate_exception():把 BrowserBridge 异常翻译成 ToolResult
"""

from __future__ import annotations

from typing import Any

from pyagent.tools.base import ToolResult
from pyagent.tools.browser import BrowserSettings, get_bridge
from pyagent.tools.browser.exceptions import BrowserNotConnectedError


def get_settings() -> BrowserSettings:
    """获取桥当前的 settings (单例可能未初始化 → 用默认值)。

    工具层不直接读全局 Settings,避免和 PyAgent config 耦合;
    真正启用开关在 Runtime.setup() 通过 BrowserSettings.enabled 控制。
    """
    bridge = get_bridge()
    if bridge is not None and bridge.settings is not None:
        return bridge.settings
    return BrowserSettings()


def not_connected_result() -> ToolResult:
    """统一的"桥未连接"错误响应。

    设计目标:LLM 看到错误后**调一次** browser_install_hint 拿完整指引,
    而非反复重试当前工具 (符合 user memory "do_* 错误 next_prompt 写法")。
    """
    return ToolResult(
        content=(
            "浏览器桥未连接。请先调用 browser_install_hint 工具获取安装指引,"
            "按说明加载 Chrome 扩展,然后调用 browser_status 验证连接。"
        ),
        is_error=True,
        details={
            "error": "not_connected",
            "hint_tool": "browser_install_hint",
        },
    )


async def async_ensure_bridge() -> tuple[Any | None, ToolResult | None]:
    """异步版 ensure_bridge — 等到 bridge server ready。

    行为:
    1. enabled=False → err
    2. bridge 不存在 → init_bridge() 创建 + connect
    3. bridge 已存在但 server 没起 → await bridge.connect() 同步等到 ready
    4. server 起来后返回 (bridge, None)

    注意:不会启 Chrome — Chrome 由 ``bridge.navigate()/execute_js()``
    内部 ``ensure_extension_connected`` 触发。本方法只保证 server listen。
    """
    from pyagent.tools.browser import BrowserSettings, init_bridge

    settings = get_settings()
    if not settings.enabled:
        return None, ToolResult(
            content=(
                "浏览器工具未启用。请在 settings.json 设置 "
                "`browser.enabled=true`,或设置环境变量 PYAGENT_BROWSER__ENABLED=true。"
            ),
            is_error=True,
            details={"error": "disabled"},
        )
    bridge = get_bridge()
    if bridge is None:
        bridge = await init_bridge(BrowserSettings())
    if bridge is None:
        return None, not_connected_result()
    if not bridge.is_connected():
        try:
            await bridge.connect()
        except Exception:
            return None, not_connected_result()
    if not bridge.is_connected():
        return None, not_connected_result()
    return bridge, None


def translate_exception(exc: Exception) -> ToolResult:
    """把 BrowserBridge 抛出的异常翻译成 ToolResult。

    用于 try / except 块末尾,避免每个工具重复写翻译逻辑。
    """
    if isinstance(exc, BrowserNotConnectedError):
        return not_connected_result()
    from pyagent.tools.browser.exceptions import (
        BrowserDeliveryError,
        BrowserExecutionError,
        BrowserProtocolError,
        BrowserTimeoutError,
    )

    if isinstance(exc, BrowserTimeoutError):
        return ToolResult(
            content=(
                f"浏览器执行超时: {exc}。"
                "请考虑: 减小 JS 复杂度 / 增加 timeout / "
                "确认页面已加载完毕。data.timeout=true 表示浏览器侧 30s 未触发 wait 事件。"
            ),
            is_error=True,
            details={"error": "timeout"},
        )
    if isinstance(exc, BrowserDeliveryError):
        return ToolResult(
            content=(
                f"指令未送达浏览器: {exc}。"
                "可能原因:扩展已断开 / 浏览器已关闭 / WS 端口不匹配。"
                "请重新调用 browser_status 验证连接。"
            ),
            is_error=True,
            details={"error": "delivery"},
        )
    if isinstance(exc, BrowserExecutionError):
        return ToolResult(
            content=f"浏览器侧 JS 执行失败: {exc}",
            is_error=True,
            details={"error": "execution"},
        )
    if isinstance(exc, BrowserProtocolError):
        return ToolResult(
            content=f"浏览器协议错误: {exc}",
            is_error=True,
            details={"error": "protocol"},
        )
    # 兜底
    return ToolResult(
        content=f"浏览器桥调用失败: {exc}",
        is_error=True,
        details={"error": "unknown", "exception_type": type(exc).__name__},
    )

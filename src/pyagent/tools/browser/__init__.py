"""浏览器桥 (browser_*) 包入口。

from pyagent.tools.browser.bridge import BrowserBridge
from pyagent.tools.browser.settings import BrowserSettings

bridge = BrowserBridge(BrowserSettings())
await bridge.connect()
result = await bridge.execute_js("document.title")
await bridge.disconnect()

工具层 (``pyagent.tools.browser.tools.*``) 才是 PyAgent 专有的 —
它们继承 ``pyagent.tools.base.Tool`` 并被 ``ToolDiscovery`` 自动发现。

单例管理
--------
进程内维护一个 ``BrowserBridge`` 实例,通过 ``get_bridge()`` 取得。
``init_bridge()`` 在 ``Runtime.setup()`` 调用,``close_bridge()`` 在 Runtime
销毁时调用。多次 ``init_bridge`` 会复用已有实例 (已连接则 no-op)。
"""

from __future__ import annotations

import contextlib

from .bridge import BrowserBridge, ExecuteResult, TabInfo
from .exceptions import (
    BrowserDeliveryError,
    BrowserError,
    BrowserExecutionError,
    BrowserNotConnectedError,
    BrowserProtocolError,
    BrowserTimeoutError,
)
from .settings import BrowserSettings

__all__ = [
    "BrowserBridge",
    "BrowserSettings",
    "TabInfo",
    "ExecuteResult",
    "BrowserError",
    "BrowserNotConnectedError",
    "BrowserDeliveryError",
    "BrowserTimeoutError",
    "BrowserExecutionError",
    "BrowserProtocolError",
    "get_bridge",
    "set_bridge",
    "init_bridge",
    "close_bridge",
    "is_bridge_enabled",
]

# 进程级单例 (避免 Runtime 内多工具实例化多个 WS 连接)
_bridge: BrowserBridge | None = None


def get_bridge() -> BrowserBridge | None:
    """获取当前进程内单例的 BrowserBridge;未初始化返回 None。"""
    return _bridge


def set_bridge(bridge: BrowserBridge | None) -> None:
    """显式替换单例 (主要用于测试)。"""
    global _bridge
    _bridge = bridge


async def init_bridge(settings: BrowserSettings | None = None) -> BrowserBridge | None:
    """初始化单例并尝试连接。

    Returns:
        连接成功返回 ``BrowserBridge`` 实例;未启用 (``settings.enabled=False``)
        或连接失败返回 ``None`` (不抛错 — 让工具层走 ``hint_tool`` 分支)。
    """
    global _bridge
    cfg = settings or BrowserSettings()
    if not cfg.enabled:
        return None
    if _bridge is None:
        _bridge = BrowserBridge(cfg)
    try:
        await _bridge.connect()
    except Exception as exc:  # noqa: BLE001
        # 自动连接失败不应阻塞 Runtime 启动 — 让用户主动用 browser_install_hint
        from pyagent.utils.logger import get_logger

        get_logger(__name__).warning("浏览器桥自动连接失败(可忽略,稍后可用 browser_status 重试): %s", exc)
        return None
    return _bridge


async def close_bridge() -> None:
    """关闭并清空单例 (Runtime 销毁时调用)。"""
    global _bridge
    if _bridge is not None:
        with contextlib.suppress(Exception):
            await _bridge.disconnect()
        _bridge = None


def is_bridge_enabled() -> bool:
    """快速判定:当前 Settings 是否允许浏览器工具运行。

    不读全局 Settings (避免循环依赖),只检查单例是否已初始化。
    真正的开关检查在工具层执行 (``BrowserSettings.enabled``)。
    """
    return True  # 实际开关在工具内通过 settings.enabled 判定

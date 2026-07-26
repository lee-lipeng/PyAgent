"""浏览器桥 (browser_*) 异常定义。

与 pyagent.tools.exceptions 中的通用 Tool 异常解耦:
- 这里是 BrowserBridge 内部抛出的协议层错误
- 工具层负责把这些异常翻译成 ToolResult(is_error=True, ...)
"""

from __future__ import annotations


class BrowserError(Exception):
    """浏览器桥所有异常的基类。"""


class BrowserNotConnectedError(BrowserError):
    """浏览器桥未建立连接 / 连接已断开。

    常见原因:
    - 用户未加载 Chrome 扩展
    - WS 端口不匹配
    - 远程 master 未启动
    """


class BrowserDeliveryError(BrowserError):
    """指令在超时时间内**未送达**浏览器侧 (未收到 ACK)。

    与 BrowserTimeoutError 的区别:
    - DeliveryError: 浏览器可能根本没收到指令 (扩展未运行 / WS 断)
    - TimeoutError: 浏览器收到并执行了,但 JS 跑得慢 / 死循环
    """


class BrowserTimeoutError(BrowserError):
    """指令已送达 (收到 ACK),但执行超时未返回结果。"""


class BrowserExecutionError(BrowserError):
    """浏览器侧 JS 执行抛错。

    例如:document.querySelector(...) 返回 null 后续调用属性崩溃。
    """


class BrowserProtocolError(BrowserError):
    """协议层错误 (非法 JSON / 缺字段 / 不支持的消息类型)。"""

"""Spinner — 加载动画。

在 Agent 思考时显示加载动画，提升用户体验。
使用 rich Status 实现，支持自定义样式。
"""

from __future__ import annotations

import contextlib

from rich.console import Console


@contextlib.contextmanager
def spinner(console: Console, text: str = "思考中"):
    """加载动画上下文管理器。

    用法::

        with spinner(console, "思考中"):
            response = await agent.run(query)

    Args:
        console: rich Console 实例。
        text: 提示文本，会以 `…` 结尾。
    """
    from rich.status import Status

    status = Status(
        f"[bold cyan]⬢[/] [white]{text}[/][dim]…[/]",
        console=console,
        spinner="dots12",
        spinner_style="cyan",
    )
    status.start()
    try:
        yield status
    finally:
        status.stop()

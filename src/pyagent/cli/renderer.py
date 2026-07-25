"""Renderer — rich 终端渲染。

设计风格参考 Claude Code CLI / Pi Agent / Gemini CLI：
- 启动横幅：Panel + 渐变标题 + 状态指标
- 用户输入：单行 `❯` 提示符，紧凑、不滥用边框
- Agent 回复：Markdown 渲染，Panel 包裹，左侧色条
- 流式输出：直接逐字打印，避免 rich 解析干扰
- 工具调用：状态感知的紧凑显示（pending → done / error）
- 错误 / 警告 / 成功：语义化前缀 + 颜色
"""

from __future__ import annotations

import sys
from typing import Any

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel


class Renderer:
    """终端渲染器。

    封装 rich Console，提供 Agent 专用的输出方法。
    所有方法只负责"显示"，不修改任何业务数据。
    """

    # ── 颜色主题 ──────────────────────────────────────────────
    C_USER = "bold cyan"
    C_AGENT = "bold green"
    C_TOOL = "bold yellow"
    C_ERROR = "bold red"
    C_DIM = "dim"
    C_INFO = "cyan"
    C_WARN = "bold yellow"
    C_OK = "bold green"
    C_BORDER = "green"

    # 进度条字符
    BAR_FULL = "█"
    BAR_EMPTY = "░"

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    # ── 启动横幅 ──────────────────────────────────────────────

    def banner(
        self,
        title: str,
        subtitle: str = "",
        stats: list[tuple[str, str]] | None = None,
    ) -> None:
        """打印启动横幅——Panel + 标题 + 状态指标。

        Args:
            title: 主标题（如 "PyAgent"）。
            subtitle: 副标题（如 "AI 编程助手"）。
            stats: 状态指标列表 [(label, value), ...]。
        """
        header = f"[bold green]{title}[/]"
        if subtitle:
            header += f"  [dim]·[/]  [white]{subtitle}[/]"

        body_parts: list[Any] = [header]
        if stats:
            stat_line = "  ".join(f"[dim]{label}:[/] [{self._stat_color(value)}]{value}[/]" for label, value in stats)
            body_parts.append("")
            body_parts.append(stat_line)

        panel = Panel(
            Group(*body_parts),
            box=ROUNDED,
            border_style="green",
            padding=(1, 2),
            expand=False,
        )
        self.console.print()
        self.console.print(panel)

    @staticmethod
    def _stat_color(value: str) -> str:
        """根据值返回合适的颜色。"""
        if value.isdigit():
            return "yellow"
        return "white"

    # ── 用户输入 ──────────────────────────────────────────────

    def user_input(self, text: str) -> None:
        """渲染用户输入——单行紧凑 `❯` 前缀。"""
        self.console.print()
        self.console.print(f"[{self.C_USER}]❯[/] [cyan]{text}[/]")

    def prompt(self) -> str:
        """打印输入提示符 `❯ `，返回后续输入（raw）。"""
        self.console.print("[bold cyan]❯[/] ", end="")
        return sys.stdin.readline().rstrip("\n")

    # ── Agent 回复 ────────────────────────────────────────────

    def assistant_response(self, text: str) -> None:
        """渲染 Agent 回复（支持 Markdown），用 Panel 包裹。"""
        body = Markdown(text)
        panel = Panel(
            body,
            title="[bold green]⬢ PyAgent[/]",
            title_align="left",
            border_style="green",
            box=ROUNDED,
            padding=(0, 1),
        )
        self.console.print()
        self.console.print(Padding(panel, (0, 0, 0, 2)))

    def stream_start(self) -> None:
        """开始流式输出——打印 Panel 顶部 + 标识。"""
        self.console.print()
        self.console.print(f"[{self.C_AGENT}]⬢ PyAgent[/]")

    def stream_chunk(self, text: str) -> None:
        """输出一个流式文本片段（实时打印，不换行）。

        直接用 sys.stdout.write 绕过 rich 渲染管线，
        避免 rich 对 Markdown 标记和 emoji 的解析导致内容丢失。
        """
        sys.stdout.write(text)
        sys.stdout.flush()

    def stream_end(self) -> None:
        """结束流式输出，换行收尾。"""
        self.console.print()

    # ── 工具调用 ──────────────────────────────────────────────

    def tool_call(self, tool_name: str, args: dict[str, Any]) -> None:
        """渲染工具调用——紧凑的多行展示。

        格式：
            🔧 tool_name
              ├─ key=value
              └─ key=value
        """
        self.console.print()
        self.console.print(f"  [{self.C_TOOL}]🔧 {tool_name}[/]")

        if not args:
            return

        items = list(args.items())
        for i, (k, v) in enumerate(items):
            is_last = i == len(items) - 1
            branch = "└─" if is_last else "├─"
            sv = self._format_value(v)
            self.console.print(f"     [dim]{branch}[/] [cyan]{k}[/] [dim]=[/] {sv}")

    def tool_result(self, tool_name: str, content: str, is_error: bool = False) -> None:
        """渲染工具执行结果——紧凑的缩进显示。"""
        icon = "✗" if is_error else "✓"
        color = "red" if is_error else "green"
        display = content if len(content) <= 500 else content[:500] + "\n  …(截断)"
        lines = display.strip().splitlines()
        if lines:
            self.console.print(f"  [{color}]{icon}[/] [dim]{lines[0]}[/]")
            for line in lines[1:]:
                self.console.print(f"     [dim]{line}[/]")
        else:
            self.console.print(f"  [{color}]{icon}[/]")

    @staticmethod
    def _format_value(v: Any) -> str:
        """格式化工具参数值：截断 + 引号包裹（多行 / 含空格时）。"""
        sv = str(v)
        if len(sv) > 80:
            sv = sv[:77] + "..."
        if "\n" in sv or " " in sv:
            sv = sv.replace("\n", "\\n")
            return f'"{sv}"'
        return sv

    # ── 错误 / 警告 / 成功 / 提示 ──────────────────────────────

    def error(self, text: str) -> None:
        """渲染错误信息——红色前缀。"""
        self.console.print()
        self.console.print(f"  [{self.C_ERROR}]✗ 错误[/]  {text}")

    def warning(self, text: str) -> None:
        """渲染警告信息。"""
        self.console.print(f"  [{self.C_WARN}]⚠ {text}[/]")

    def success(self, text: str) -> None:
        """渲染成功信息。"""
        self.console.print(f"  [{self.C_OK}]✓ {text}[/]")

    def info(self, text: str) -> None:
        """渲染提示信息——暗色文字。"""
        self.console.print(f"  [{self.C_DIM}]{text}[/]")

    def hint(self, label: str, value: str) -> None:
        """渲染带标签的提示（如快捷键）。"""
        self.console.print(f"  [dim]{label}[/] [cyan]{value}[/]")

    # ── 键值表 / 进度条 ───────────────────────────────────────

    def keyvalue(
        self,
        items: list[tuple[str, str]],
        title: str | None = None,
    ) -> None:
        """渲染紧凑的键值列表（用于 /info、状态展示）。

        Args:
            items: (key, value) 列表。
            title: 可选标题。
        """
        body = "\n".join(f"[bold]{key}[/]  [cyan]{value}[/]" for key, value in items)
        panel = Panel(
            body,
            title=f"[bold]{title}[/]" if title else None,
            title_align="left",
            border_style="dim",
            box=ROUNDED,
            padding=(0, 2),
        )
        self.console.print()
        self.console.print(panel)

    def progress_bar(
        self,
        used: int,
        total: int,
        width: int = 24,
    ) -> str:
        """渲染文本进度条，返回格式化字符串。"""
        if total <= 0:
            return "[dim]N/A[/]"
        ratio = min(max(used / total, 0.0), 1.0)
        filled = int(width * ratio)
        empty = width - filled
        bar = self.BAR_FULL * filled + self.BAR_EMPTY * empty
        pct = ratio * 100
        # 颜色随百分比变化：低 = 绿、中 = 黄、高 = 红
        if pct < 60:
            color = "green"
        elif pct < 85:
            color = "yellow"
        else:
            color = "red"
        return f"[{color}]{bar}[/] [dim]{pct:.0f}%[/]"

    # ── 状态 / 杂项 ───────────────────────────────────────────

    def status(self, text: str) -> Any:
        """返回 rich Status 上下文管理器。"""
        from rich.status import Status

        return Status(f"[dim]{text}…[/]", console=self.console, spinner="dots")

    def blank(self) -> None:
        """打印空行。"""
        self.console.print()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """直接输出。"""
        self.console.print(*args, **kwargs)

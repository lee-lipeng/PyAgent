"""list_dir 工具：列出目录内容。

返回目录下的文件和子目录列表，带类型标记。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class ListDirArgs(BaseModel):
    """list_dir 参数。"""

    path: str = Field(default=".", description="要列出的目录路径")
    all: bool = Field(default=False, description="是否显示隐藏文件（以 . 开头）")


@tool("list_dir", description="列出目录内容，返回文件和子目录列表。")
class ListDirTool(Tool):
    """目录列表工具。"""

    parameters_model = ListDirArgs

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        dir_path = Path(args.get("path", ".")).expanduser().resolve()
        show_all = args.get("all", False)

        if not dir_path.exists():
            return ToolResult(
                content=f"路径不存在: {dir_path}",
                is_error=True,
                details={"error": "path_not_found", "path": str(dir_path)},
            )

        if not dir_path.is_dir():
            return ToolResult(
                content=f"路径不是目录: {dir_path}",
                is_error=True,
                details={"error": "not_a_dir", "path": str(dir_path)},
            )

        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines: list[str] = []

        for entry in entries:
            # 隐藏文件过滤
            if not show_all and entry.name.startswith("."):
                continue

            if entry.is_dir():
                lines.append(f"  {entry.name}/")
            else:
                # 显示文件大小
                try:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f}K"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f}M"
                    lines.append(f"  {entry.name} ({size_str})")
                except OSError:
                    lines.append(f"  {entry.name}")

        if not lines:
            return ToolResult(
                content="(空目录)" if show_all else "(空目录或全部为隐藏文件)",
                details={"path": str(dir_path), "count": 0},
            )

        return ToolResult(
            content="\n".join(lines),
            details={"path": str(dir_path), "count": len(lines)},
        )

"""read_file 工具：读取文件内容。

支持指定行范围，自动检测编码（优先 UTF-8）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class ReadFileArgs(BaseModel):
    """read_file 参数。"""

    path: str = Field(description="要读取的文件路径（相对或绝对）")
    start_line: int = Field(default=1, ge=1, description="起始行号（从 1 开始）")
    end_line: int | None = Field(default=None, ge=1, description="结束行号（含），不传则读到末尾")


@tool("read_file", description="读取文件内容，可指定行范围。")
class ReadFileTool(Tool):
    """读取文件内容工具。"""

    parameters_model = ReadFileArgs

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        from pathlib import Path

        path = Path(args["path"]).expanduser().resolve()
        start_line = args.get("start_line", 1)
        end_line = args.get("end_line")

        if not path.exists():
            return ToolResult(
                content=f"文件不存在: {path}",
                is_error=True,
                details={"error": "file_not_found", "path": str(path)},
            )

        if not path.is_file():
            return ToolResult(
                content=f"路径不是文件: {path}",
                is_error=True,
                details={"error": "not_a_file", "path": str(path)},
            )

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # UTF-8 失败，尝试 GBK（Windows 常见）
            try:
                content = path.read_text(encoding="gbk")
            except Exception as exc:
                return ToolResult(
                    content=f"读取文件失败（编码问题）: {exc}",
                    is_error=True,
                    details={"error": "encoding_error", "path": str(path)},
                )
        except Exception as exc:
            return ToolResult(
                content=f"读取文件失败: {exc}",
                is_error=True,
                details={"error": "read_error", "path": str(path)},
            )

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # 转为 0-based 索引
        start_idx = start_line - 1
        end_idx = end_line if end_line is not None else total_lines
        end_idx = min(end_idx, total_lines)

        selected = lines[start_idx:end_idx]
        result_text = "".join(selected)

        # 添加行号前缀，方便 LLM 引用
        numbered_lines = []
        for i, line in enumerate(selected, start=start_line):
            # 去掉末尾换行再加行号，最后统一加回
            stripped = line.rstrip("\n\r")
            numbered_lines.append(f"{i:6d}: {stripped}")
        if numbered_lines:
            result_text = "\n".join(numbered_lines)

        return ToolResult(
            content=result_text,
            details={
                "path": str(path),
                "start_line": start_line,
                "end_line": start_line + len(selected) - 1 if selected else start_line,
                "total_lines": total_lines,
            },
        )

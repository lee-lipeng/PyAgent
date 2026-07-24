"""write_file 工具：写入文件内容。

会自动创建父目录。如果文件已存在则覆盖。

路径语义遵循标准 Path 解析：
- 绝对路径：原样使用（用户自行负责）
- 相对路径：相对于当前工作目录解析

如需把 AI 生成的文件集中存到 ~/.pyagent/output/，请显式使用绝对路径，
或在提示中要求模型使用 ~/.pyagent/output/... 作为前缀。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class WriteFileArgs(BaseModel):
    """write_file 参数。"""

    path: str = Field(description="要写入的文件路径。绝对路径原样使用，相对路径相对当前工作目录。")
    content: str = Field(description="文件内容（完整内容，非增量）")


def _resolve_path(requested: str):
    """把用户给的路径按 Path 语义解析，自动创建父目录。

    - 绝对路径：原样解析
    - 相对路径：相对于 CWD
    """
    from pathlib import Path

    resolved = Path(requested).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


@tool("write_file", description="写入文件内容。绝对路径原样使用，相对路径相对当前工作目录。")
class WriteFileTool(Tool):
    """写入文件工具。"""

    parameters_model = WriteFileArgs

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        content = args["content"]

        try:
            path = _resolve_path(args["path"])
        except Exception as exc:
            return ToolResult(
                content=f"路径解析失败: {exc}",
                is_error=True,
                details={"error": "path_error", "path": args["path"]},
            )

        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(
                content=f"写入文件失败: {exc}",
                is_error=True,
                details={"error": "write_error", "path": str(path)},
            )

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return ToolResult(
            content=f"已写入文件\n 位置: {path}\n {line_count} 行，{len(content)} 字节",
            details={
                "path": str(path),
                "bytes": len(content),
                "lines": line_count,
            },
        )

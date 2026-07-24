"""find_files 工具：按名称模式查找文件。

用 glob 模式递归查找，返回匹配的文件路径列表。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class FindFilesArgs(BaseModel):
    """find_files 参数。"""

    pattern: str = Field(description="文件名 glob 模式，如 '*.py' 或 'test_*.py'")
    path: str = Field(default=".", description="搜索根目录，默认当前目录")
    max_results: int = Field(default=100, ge=1, le=1000, description="最大返回文件数")


@tool("find_files", description="按文件名 glob 模式递归查找文件。")
class FindFilesTool(Tool):
    """文件查找工具。"""

    parameters_model = FindFilesArgs

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        pattern = args["pattern"]
        search_path = Path(args.get("path", ".")).expanduser().resolve()
        max_results = args.get("max_results", 100)

        if not search_path.exists():
            return ToolResult(
                content=f"路径不存在: {search_path}",
                is_error=True,
                details={"error": "path_not_found", "path": str(search_path)},
            )

        skip_dirs = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            ".tox",
            "dist",
            "build",
        }
        matches: list[Path] = []

        for path in search_path.rglob(pattern):
            # 跳过忽略目录
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.is_file():
                matches.append(path)
                if len(matches) >= max_results:
                    break

        if not matches:
            return ToolResult(
                content="未找到匹配文件。",
                details={"pattern": pattern, "path": str(search_path), "count": 0},
            )

        # 显示相对路径
        rel_paths = []
        for p in matches:
            try:
                rel_paths.append(str(p.relative_to(search_path)))
            except ValueError:
                rel_paths.append(str(p))

        header = f"找到 {len(matches)} 个文件"
        if len(matches) >= max_results:
            header += f"（已达上限 {max_results}）"
        body = "\n".join(rel_paths)
        return ToolResult(
            content=f"{header}\n{body}",
            details={
                "pattern": pattern,
                "path": str(search_path),
                "count": len(matches),
                "truncated": len(matches) >= max_results,
            },
        )

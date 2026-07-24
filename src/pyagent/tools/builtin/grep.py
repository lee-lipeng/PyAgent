"""grep 工具：正则搜索文件内容。

递归搜索目录，返回匹配行及行号。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class GrepArgs(BaseModel):
    """grep 参数。"""

    pattern: str = Field(description="正则表达式模式")
    path: str = Field(default=".", description="搜索目录或文件路径，默认当前目录")
    include: str | None = Field(default=None, description="文件名 glob 过滤，如 '*.py'")
    max_results: int = Field(default=200, ge=1, le=1000, description="最大返回匹配数")


@tool("grep", description="正则搜索文件内容，递归扫描目录，返回匹配行及行号。")
class GrepTool(Tool):
    """正则搜索工具。"""

    parameters_model = GrepArgs

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        pattern_str = args["pattern"]
        search_path = Path(args.get("path", ".")).expanduser().resolve()
        include = args.get("include")
        max_results = args.get("max_results", 200)

        if not search_path.exists():
            return ToolResult(
                content=f"路径不存在: {search_path}",
                is_error=True,
                details={"error": "path_not_found", "path": str(search_path)},
            )

        try:
            regex = re.compile(pattern_str)
        except re.error as exc:
            return ToolResult(
                content=f"正则表达式无效: {exc}",
                is_error=True,
                details={"error": "invalid_regex", "pattern": pattern_str},
            )

        # 收集要搜索的文件
        if search_path.is_file():
            files = [search_path]
        else:
            files = self._collect_files(search_path, include)

        matches: list[str] = []
        total_matches = 0

        for file_path in files:
            if total_matches >= max_results:
                break
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue

            for line_no, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    # 显示相对路径更简洁
                    try:
                        rel_path = file_path.relative_to(search_path)
                    except ValueError:
                        rel_path = file_path
                    matches.append(f"{rel_path}:{line_no}: {line}")
                    total_matches += 1
                    if total_matches >= max_results:
                        break

        if not matches:
            return ToolResult(
                content="未找到匹配。",
                details={
                    "pattern": pattern_str,
                    "path": str(search_path),
                    "matches": 0,
                },
            )

        header = f"找到 {total_matches} 处匹配"
        if total_matches >= max_results:
            header += f"（已达上限 {max_results}）"
        body = "\n".join(matches)
        return ToolResult(
            content=f"{header}\n{body}",
            details={
                "pattern": pattern_str,
                "path": str(search_path),
                "matches": total_matches,
                "truncated": total_matches >= max_results,
            },
        )

    @staticmethod
    def _collect_files(root: Path, include: str | None) -> list[Path]:
        """递归收集文件，跳过常见忽略目录。"""
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
        files: list[Path] = []
        for path in root.rglob("*"):
            # 跳过忽略目录下的文件
            if any(part in skip_dirs for part in path.parts):
                continue
            if not path.is_file():
                continue
            if include and not path.match(include):
                continue
            files.append(path)
        return files

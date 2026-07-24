"""edit_file 工具：精确字符串替换编辑。

在文件中查找 old_string，替换为 new_string。
要求 old_string 在文件中唯一出现，否则报错。

路径语义同 write_file：
- 绝对路径：原样使用（用户自行负责）
- 相对路径：相对于当前工作目录解析
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.builtin.write import _resolve_path
from pyagent.tools.decorators import tool


class EditFileArgs(BaseModel):
    """edit_file 参数。"""

    path: str = Field(description="要编辑的文件路径。绝对路径原样使用，相对路径相对当前工作目录。")
    old_string: str = Field(description="要替换的原始文本（必须在文件中唯一出现）")
    new_string: str = Field(description="替换后的文本")


@tool(
    "edit_file",
    description="精确字符串替换编辑。绝对路径原样使用，相对路径相对当前工作目录。old_string必须在文件中唯一出现。",
)
class EditFileTool(Tool):
    """精确替换编辑工具。"""

    parameters_model = EditFileArgs

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        old_string = args["old_string"]
        new_string = args["new_string"]

        try:
            path = _resolve_path(args["path"])
        except Exception as exc:
            return ToolResult(
                content=f"路径解析失败: {exc}",
                is_error=True,
                details={"error": "path_error", "path": args["path"]},
            )

        if not path.exists():
            return ToolResult(
                content=f"文件不存在: {path}",
                is_error=True,
                details={"error": "file_not_found", "path": str(path)},
            )

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
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

        # 检查 old_string 出现次数
        occurrences = content.count(old_string)
        if occurrences == 0:
            return ToolResult(
                content="未找到要替换的文本。请检查 old_string 是否正确。",
                is_error=True,
                details={"error": "string_not_found", "path": str(path)},
            )
        if occurrences > 1:
            return ToolResult(
                content=f"old_string 在文件中出现 {occurrences} 次，无法唯一定位。请提供更多上下文使其唯一。",
                is_error=True,
                details={
                    "error": "string_not_unique",
                    "occurrences": occurrences,
                    "path": str(path),
                },
            )

        # 执行替换
        new_content = content.replace(old_string, new_string, 1)

        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(
                content=f"写入文件失败: {exc}",
                is_error=True,
                details={"error": "write_error", "path": str(path)},
            )

        return ToolResult(
            content=f"✅ 已替换 1 处文本\n📍 位置: {path}",
            details={
                "path": str(path),
                "old_length": len(old_string),
                "new_length": len(new_string),
            },
        )

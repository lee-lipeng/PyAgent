"""Tool 基类和 ToolResult。

Tool 是所有工具的基类，子类需实现 execute() 方法。
工具参数用 pydantic BaseModel 定义，自动生成 JSON Schema 给 LLM。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """工具执行结果。

    Attributes:
        content: 返回给 LLM 的文本内容。
        details: 附加元数据（不发给 LLM，供 Hook/日志使用）。
        is_error: 是否为错误结果。为 True 时 LLM 会看到错误信息。
        terminate: 是否提示 Agent 在本批工具完成后停止。
                   仅当本批所有工具都 terminate=True 时才真正停止。
    """

    content: str
    details: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False
    terminate: bool = False


class Tool(ABC):
    """工具基类。

    子类需定义：
        name: 工具名（LLM 调用时使用）
        description: 工具描述（LLM 看到的说明）
        parameters_model: pydantic BaseModel 类，定义参数 schema
        execution_mode: 执行模式：parallel（默认）或 sequential
        execute(): 执行逻辑，返回 ToolResult

    示例::

        class ReadFileArgs(BaseModel):
            path: str = Field(description="文件路径")

        class ReadFileTool(Tool):
            name = "read_file"
            description = "读取文件内容"
            parameters_model = ReadFileArgs

            async def execute(self, tool_call_id, args, signal, on_update):
                content = Path(args["path"]).read_text(encoding="utf-8")
                return ToolResult(content=content)
    """

    name: str = ""
    description: str = ""
    parameters_model: type[BaseModel] | None = None
    execution_mode: str = "parallel"

    @abstractmethod
    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        """执行工具逻辑。

        Args:
            tool_call_id: 本次调用的唯一 ID。
            args: LLM 传来的参数（已校验）。
            signal: 取消信号，工具可检查 signal.is_set() 提前终止。
            on_update: 流式进度回调，工具可中途报告进度。

        Returns:
            ToolResult: 执行结果。

        Raises:
            Exception: 工具失败时直接抛异常，Agent 会自动捕获并报告给 LLM。
        """
        ...

    def get_schema(self) -> dict[str, Any]:
        """生成 OpenAI function calling 格式的工具 schema。"""
        parameters: dict[str, Any] = {}
        if self.parameters_model is not None:
            from pyagent.tools.schema import model_to_openai_schema

            parameters = model_to_openai_schema(self.parameters_model)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def validate_args(self, raw_args: dict[str, Any]) -> dict[str, Any]:
        """用 pydantic 模型校验参数。

        如果未定义 parameters_model，直接返回原始 args。
        """
        if self.parameters_model is None:
            return raw_args

        validated = self.parameters_model.model_validate(raw_args)
        return validated.model_dump()

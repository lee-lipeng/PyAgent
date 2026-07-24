"""run_bash 工具：执行 shell 命令。

在系统默认 shell 中执行命令，捕获 stdout/stderr 和退出码。
有超时保护，默认 120 秒。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class RunBashArgs(BaseModel):
    """run_bash 参数。"""

    command: str = Field(description="要执行的 shell 命令")
    timeout: int = Field(default=120, ge=1, le=600, description="超时秒数，默认 120")
    cwd: str | None = Field(default=None, description="工作目录，不传则用当前目录")


@tool("run_bash", description="执行 shell 命令，返回 stdout/stderr 和退出码。")
class RunBashTool(Tool):
    """Shell 命令执行工具。"""

    parameters_model = RunBashArgs
    execution_mode = "sequential"  # shell 命令串行执行更安全

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        signal: asyncio.Event | None = None,
        on_update: Callable[[ToolResult], None] | None = None,
    ) -> ToolResult:
        command = args["command"]
        timeout = args.get("timeout", 120)
        cwd = args.get("cwd")

        # 根据平台选择 shell
        shell_cmd = ["cmd", "/c", command] if os.name == "nt" else ["bash", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except Exception as exc:
            return ToolResult(
                content=f"启动命令失败: {exc}",
                is_error=True,
                details={"error": "spawn_error", "command": command},
            )

        # 同时监听 communicate 完成 / abort signal / 超时
        comm_task = asyncio.create_task(proc.communicate())
        signal_task = asyncio.create_task(signal.wait()) if signal is not None else None

        try:
            # 构造等待集合
            wait_set = {comm_task}
            if signal_task is not None:
                wait_set.add(signal_task)

            done, _pending = await asyncio.wait(
                wait_set,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # abort signal 触发
            if signal is not None and signal.is_set():
                proc.kill()
                await proc.wait()
                return ToolResult(
                    content=f"命令被用户中止: {command}",
                    is_error=True,
                    details={"error": "aborted", "command": command},
                )

            # 超时
            if not done:
                proc.kill()
                await proc.wait()
                return ToolResult(
                    content=f"命令超时（{timeout}秒），已终止。命令: {command}",
                    is_error=True,
                    details={"error": "timeout", "command": command, "timeout": timeout},
                )

            # 正常完成
            stdout_bytes, stderr_bytes = comm_task.result()

        finally:
            comm_task.cancel()
            if signal_task is not None:
                signal_task.cancel()

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return_code = proc.returncode

        # 构造返回内容
        parts = []
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        if not parts:
            parts.append("(无输出)")
        parts.append(f"退出码: {return_code}")

        is_error = return_code != 0
        return ToolResult(
            content="\n".join(parts),
            is_error=is_error,
            details={
                "command": command,
                "return_code": return_code,
                "stdout_length": len(stdout),
                "stderr_length": len(stderr),
            },
        )

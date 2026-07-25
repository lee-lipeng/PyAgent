"""RuntimeContext — 运行时上下文。

在 Agent 运行期间传递的共享状态，包含：
- 当前会话
- 当前用户输入
- 取消信号（abort）
- 改向队列（steering）
- 元数据（供 Hook 和工具使用）

打断机制设计（参考 Pi Agent）：
- steer：用户在 Agent 运行期间提交的新输入，入队后在 turn 边界注入，
  不打断当前 LLM 流式输出或正在执行的工具批次。
- abort：通过 cancel_signal 立即中止，工具可监听 signal 提前退出。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pyagent.session.types import Session


@dataclass
class RuntimeContext:
    """运行时上下文。

    每次用户输入创建一个新实例，贯穿整个 Agent 循环。
    """

    # 当前会话（Runtime 会在未传时自动创建 ephemeral session）
    session: Session

    # 用户输入文本
    query: str = ""

    # 取消信号（abort）
    cancel_signal: asyncio.Event = field(default_factory=asyncio.Event)

    # 改向队列（steering）——FIFO，turn 边界统一 drain
    steering_queue: list[str] = field(default_factory=list)

    # 自由元数据，供 Hook 和工具使用
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_cancelled(self) -> bool:
        """检查是否被取消。"""
        return self.cancel_signal.is_set()

    def cancel(self) -> None:
        """发出取消信号（abort）。"""
        self.cancel_signal.set()

    def steer(self, text: str) -> None:
        """将用户输入加入改向队列。

        在 Agent 运行期间调用，文本不会立即注入消息历史，
        而是等到当前 turn 的所有工具调用完成后、下一轮 LLM 调用前
        统一注入（drain），避免破坏 assistant(tool_calls) ↔ tool_result 配对。
        """
        self.steering_queue.append(text)

    def drain_steering(self) -> list[str]:
        """取出并清空改向队列（FIFO）。"""
        items = self.steering_queue[:]
        self.steering_queue.clear()
        return items

    def has_steering(self) -> bool:
        """改向队列是否非空。"""
        return len(self.steering_queue) > 0

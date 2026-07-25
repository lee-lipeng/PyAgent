"""Core 运行时核心。

包含 Agent 的核心运行逻辑：
- Runtime: 运行时环境，组装各组件并管理生命周期
- Agent: Agent 实体，持有 LLM 客户端、工具执行器、技能管理器
- AgentLoop: Agent 循环，管理 LLM ↔ Tool 的多轮交互
- Context: 运行时上下文，传递给各组件的共享状态
- ContextBuilder: 上下文构建管道，三阶段消息构建
- CompactionManager: 上下文压缩管理器

依赖方向：
    Runtime → Agent → AgentLoop → (LLMClient, ToolExecutor, SkillManager)
    AgentLoop → ContextBuilder → (Session, TokenEstimator)
    AgentLoop → CompactionManager → (LLMClient, Session)
    所有组件 → HookManager（dispatch 事件）
"""

from pyagent.core.agent import Agent
from pyagent.core.compaction import CompactionManager, CompactionResult
from pyagent.core.context import RuntimeContext
from pyagent.core.context_builder import ContextBuilder
from pyagent.core.loop import AgentLoop
from pyagent.core.runtime import Runtime

__all__ = [
    "Agent",
    "AgentLoop",
    "CompactionManager",
    "CompactionResult",
    "ContextBuilder",
    "Runtime",
    "RuntimeContext",
]

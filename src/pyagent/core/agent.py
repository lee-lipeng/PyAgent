"""Agent — Agent 实体。

Agent 是核心实体，持有：
- LLM 客户端（LLMClient）
- 工具执行器（ToolExecutor）
- 技能管理器（SkillManager）
- 事件总线（HookManager）

Agent 本身不执行循环逻辑，循环逻辑在 AgentLoop 中。
Agent 提供数据和组件给 AgentLoop 使用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from pyagent.hooks.types import DispatchResult, Event, EventType
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    from pyagent.hooks.manager import HookManager
    from pyagent.llm.client import LLMClient
    from pyagent.skills.manager import SkillManager
    from pyagent.tools.executor import ToolExecutor

logger = get_logger(__name__)

T = TypeVar("T")


class Agent:
    """Agent 实体。

    Args:
        llm_client: LLM 客户端。
        tool_executor: 工具执行器。
        skill_manager: 技能管理器。
        hooks: 事件总线。
        system_prompt: 基础系统提示词。
        max_turns: 最大循环轮次。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_executor: ToolExecutor,
        skill_manager: SkillManager,
        hooks: HookManager,
        system_prompt: str = "",
        max_turns: int = 20,
    ) -> None:
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.skill_manager = skill_manager
        self.hooks = hooks
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    def build_system_prompt(self) -> str:
        """构建完整系统提示词。

        基础 system_prompt + 技能列表（XML 块，仅 name/description/file_path）。

        借鉴 Pi Agent 的渐进式披露设计：
        - system prompt 只注入技能描述，不注入完整正文
        - LLM 读 description 自行判断是否用 read_file 加载 SKILL.md
        """
        parts = [self.system_prompt] if self.system_prompt else []

        # 注入技能列表（仅 name/description/file_path）
        skill_block = self.skill_manager.format_for_system_prompt()
        if skill_block:
            parts.append(skill_block)

        return "\n\n".join(parts)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """获取所有工具的 schema。"""
        return self.tool_executor._registry.get_schemas()

    async def dispatch(
        self,
        event_type: EventType,
        initial: T | None = None,
        **payload: Any,
    ) -> DispatchResult[T]:
        """统一事件派发入口。

        一次调用同时拿到链最终值（result.value）与是否被取消
        （result.cancelled），调用方按需读取：

            result = await self.dispatch(EventType.BEFORE_LLM, messages,
                                         turn=turns)
            if result.cancelled:
                return LoopResult(success=False, error=result.cancel_reason, ...)
            messages = result.value  # 链式 transform 后的最终值

        Args:
            event_type: 事件类型。
            initial: 链初始值（想拿链最终结果时必传，否则默认为 None）。
            **payload: 附加上下文，会被合并到 event.payload 中。

        Returns:
            `DispatchResult`，包含 ``cancelled / cancel_reason / value``。
        """
        event = Event(type=event_type, payload=payload)
        return await self.hooks.dispatch(event, initial)

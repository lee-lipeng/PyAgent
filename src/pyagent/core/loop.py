"""AgentLoop — Agent 循环核心, 管理 LLM ↔ Tool 的多轮交互。

循环流程：
    1. 构建消息（system + 历史 + user）—— 通过 ContextBuilder
    2. 检查上下文使用量，必要时触发压缩 —— 通过 CompactionManager
    3. 调用 LLM（流式输出）
    4. 如果 LLM 返回工具调用 → 执行工具 → 把结果加入消息 → 回到 1
    5. 如果 LLM 返回纯文本（无工具调用）→ 循环结束
    6. 达到 max_turns → 循环结束

事件流：
    BEFORE_LLM → AFTER_LLM → BEFORE_TOOL → AFTER_TOOL

借鉴 Pi Agent 的设计：
- 上下文构建通过 ContextBuilder 解耦
- 每轮 LLM 调用后检查 token 预算，自动触发压缩
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pyagent.core.context import RuntimeContext
from pyagent.core.context_builder import ContextBuilder
from pyagent.hooks.types import EventType
from pyagent.llm.token_estimator import ContextUsage
from pyagent.session import Session
from pyagent.session.messages import AssistantMessage, ToolResultMessage, UserMessage
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyagent.core.agent import Agent
    from pyagent.core.compaction import CompactionManager

logger = get_logger(__name__)


class LoopResult:
    """循环执行结果。"""

    def __init__(
        self,
        success: bool,
        final_response: str = "",
        turns: int = 0,
        stop_reason: str = "",
        error: str = "",
    ) -> None:
        self.success = success
        self.final_response = final_response
        self.turns = turns
        self.stop_reason = stop_reason
        self.error = error


class AgentLoop:
    """Agent 循环。

    集成上下文构建和压缩管理。

    Args:
        agent: Agent 实体。
        compaction_manager: 压缩管理器（可选，启用自动压缩时需要）。
    """

    def __init__(
        self,
        agent: Agent,
        compaction_manager: CompactionManager | None = None,
    ) -> None:
        self.agent = agent
        self._compaction = compaction_manager

        # 上下文使用量监控器
        self.context_usage: ContextUsage | None = None

        # 上下文构建器
        self._context_builder: ContextBuilder | None = None

    @property
    def compaction_manager(self) -> CompactionManager | None:
        """压缩管理器。"""
        return self._compaction

    @compaction_manager.setter
    def compaction_manager(self, value: CompactionManager | None) -> None:
        self._compaction = value

    def _ensure_context_builder(self, session: Session) -> ContextBuilder:
        """确保上下文构建器已初始化。

        从 Agent 的系统提示词和会话的上下文窗口配置创建 ContextBuilder。
        """
        if self._context_builder is None:
            system_prompt = self.agent.build_system_prompt()
            context_window = session.metadata.context_window
            threshold = session.metadata.compaction_threshold
            self.context_usage = ContextUsage(
                limit=context_window or 128000,
                threshold=threshold,
            )
            self._context_builder = ContextBuilder(
                system_prompt=system_prompt,
                context_usage=self.context_usage,
            )
        return self._context_builder

    async def run(
        self,
        query: str,
        session: Session,
        on_chunk: Callable[[str], None] | None = None,
        ctx: RuntimeContext | None = None,
    ) -> LoopResult:
        """执行 Agent 循环。

        每轮 LLM 调用后检查上下文使用量，
        达到阈值时自动触发压缩。

        Args:
            query: 用户输入。
            session: 会话对象（Runtime 会在用户未传时自动创建 ephemeral session）。
            on_chunk: 流式回调，每收到一个文本 chunk 调用。
            ctx: 外部传入的运行时上下文（支持 steer/abort），
                 为 None 时内部创建。Runtime.steer/abort 依赖此引用。

        Returns:
            LoopResult: 循环结果。
        """
        if ctx is None:
            ctx = RuntimeContext(query=query, session=session)

        # 初始化上下文构建器
        builder = self._ensure_context_builder(session)

        # 记录当前用户输入到会话
        session.add_message(UserMessage(content=query))

        # 获取工具 schema
        tools = self.agent.get_tool_schemas()

        turns = 0
        final_response = ""
        last_usage = None

        try:
            while turns < self.agent.max_turns:
                # 任务取消
                if ctx.is_cancelled():
                    await self.agent.dispatch(EventType.AGENT_ABORT, turns=turns)
                    return LoopResult(
                        success=False,
                        final_response=final_response,
                        turns=turns,
                        stop_reason="cancelled",
                    )

                turns += 1

                messages = builder.build(session, last_usage)

                # 单一 dispatch：handler 可取消（return HookControl）或链式修改 messages
                llm_pre = await self.agent.dispatch(
                    EventType.BEFORE_LLM,
                    initial=messages,
                    turn=turns,
                    message_count=len(messages),
                )
                if llm_pre.cancelled:
                    return LoopResult(
                        success=False,
                        turns=turns,
                        stop_reason="llm_cancelled",
                        error=llm_pre.cancel_reason,
                    )
                messages = llm_pre.value

                # 调用 LLM 流式收集，边输出边聚合
                try:
                    chunks, agg = await self.agent.llm_client.stream_and_collect(
                        messages=messages,
                        tools=tools if tools else None,
                    )
                    async for chunk in chunks:
                        if chunk.delta and on_chunk:
                            on_chunk(chunk.delta)

                    response = agg.result()
                except Exception as exc:
                    logger.exception("LLM 调用失败")
                    await self.agent.dispatch(
                        EventType.LLM_REQUEST_ERROR,
                        turn=turns,
                        error=str(exc),
                    )
                    return LoopResult(
                        success=False,
                        turns=turns,
                        stop_reason="llm_error",
                        error=str(exc),
                    )

                # 更新 usage
                last_usage = response.usage

                response = (
                    await self.agent.dispatch(
                        EventType.AFTER_LLM,
                        initial=response,
                        turn=turns,
                        has_tool_calls=response.has_tool_calls,
                        usage=response.usage.model_dump() if response.usage else {},
                    )
                ).value

                # 累加 token 用量由 UsageTrackingHook 监听 AFTER_LLM 自动处理

                # 构造 assistant 消息
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if response.content:
                    assistant_msg["content"] = response.content
                if response.has_tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": (
                                    tc.arguments if isinstance(tc.arguments, str) else json.dumps(tc.arguments)
                                ),
                            },
                        }
                        for tc in response.tool_calls
                    ]

                session.add_message(
                    AssistantMessage(
                        content=response.content,
                        tool_calls=assistant_msg.get("tool_calls"),
                    )
                )

                # 上下文压缩
                if (
                    self._compaction is not None
                    and self.context_usage is not None
                    and (self.context_usage.is_threshold_reached or self.context_usage.is_overflow)
                ):
                    logger.info(
                        "触发上下文压缩: used=%s, limit=%s",
                        self.context_usage.used,
                        self.context_usage.limit,
                    )

                    await self._compaction.compact_session(
                        session,
                        self.context_usage,
                        force=True,
                    )

                # 检查是否有工具调用
                if not response.has_tool_calls:
                    # 无工具调用，准备结束循环
                    final_response = response.content or ""

                    # 如果 steering 队列有内容，继续执行
                    if ctx.has_steering():
                        steered = ctx.drain_steering()
                        combined = "\n\n".join(f"[用户补充] {s}" for s in steered)
                        session.add_message(UserMessage(content=combined))

                        await self.agent.dispatch(EventType.AGENT_STEER, messages=steered)

                        logger.info(f"注入{len(steered)}条改向消息")
                        continue

                    return LoopResult(
                        success=True,
                        final_response=final_response,
                        turns=turns,
                        stop_reason="completed",
                    )

                # 执行工具
                tool_calls = response.tool_calls

                try:
                    batch_pre = await self.agent.dispatch(
                        EventType.TOOL_BATCH_START,
                        initial=tool_calls,
                        turn=turns,
                        count=len(tool_calls),
                    )
                    if batch_pre.cancelled:
                        return LoopResult(
                            success=False,
                            turns=turns,
                            stop_reason="tool_batch_cancelled",
                            error=batch_pre.cancel_reason,
                        )
                    tool_calls = batch_pre.value

                    results = await self.agent.tool_executor.execute_batch(
                        tool_calls,
                        signal=ctx.cancel_signal,
                    )
                    results = (
                        await self.agent.dispatch(
                            EventType.TOOL_BATCH_END,
                            initial=results,
                            turn=turns,
                            count=len(results),
                            failed_count=sum(r.is_error for r in results),
                        )
                    ).value
                except PermissionError as exc:
                    return LoopResult(
                        success=False,
                        turns=turns,
                        stop_reason="permission_denied",
                        error=str(exc),
                    )

                # 把工具结果加入消息
                for tc, result in zip(tool_calls, results, strict=True):
                    session.add_message(
                        ToolResultMessage(
                            content=result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )

                # turn 边界：drain steering 队列
                # 所有工具结果已落库，消息配对完整，此时注入用户补充输入。
                # steer 只在 turn 边界生效，不打断工具执行。
                if ctx.has_steering():
                    steered = ctx.drain_steering()
                    combined = "\n\n".join(f"[用户补充] {s}" for s in steered)
                    session.add_message(UserMessage(content=combined))

                    await self.agent.dispatch(EventType.AGENT_STEER, messages=steered)

                    logger.info(f"注入{len(steered)}条改向消息")

                if all(r.terminate for r in results):
                    final_response = next((r.content for r in results if r.content), "")
                    return LoopResult(
                        success=True,
                        final_response=final_response,
                        turns=turns,
                        stop_reason="terminated",
                    )

            # 达到最大轮次
            return LoopResult(
                success=False,
                final_response=final_response,
                turns=turns,
                stop_reason="max_turns",
                error=f"达到最大轮次 {self.agent.max_turns}",
            )

        except Exception as exc:
            logger.exception("Agent 循环异常")
            await self.agent.dispatch(EventType.ERROR, error=str(exc), stage="loop")

            return LoopResult(
                success=False,
                turns=turns,
                stop_reason="error",
                error=str(exc),
            )

"""上下文压缩管理器。

借鉴 Pi Agent 的上下文压缩设计，实现：
- 三种触发机制：threshold / overflow / manual
- findCutPoint(): 在消息列表中寻找安全切割点
- prepareCompaction() + compact(): 生成结构化摘要
- 迭代式摘要：新摘要包含旧摘要内容
- retainedTail: 保留最近 N 条消息不被压缩

压缩流程::

    消息列表: [msg0, msg1, ..., msgN]
                                    ↑ cutPoint
    ┌──────────────────────┐  ┌──────────────────┐
    │  被压缩段 (0..cut)    │  │  retainedTail    │
    │  → LLM 生成摘要       │  │  保留原样         │
    └──────────────────────┘  └──────────────────┘
              ↓
    [CompactionSummaryMessage, ...retainedTail]
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pyagent.llm.token_estimator import ContextUsage, estimate_tokens
from pyagent.session.messages import CompactionSummaryMessage, message_to_llm
from pyagent.session.types import Session
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    from pyagent.hooks.manager import HookManager
    from pyagent.llm.client import LLMClient

logger = get_logger(__name__)

# 压缩摘要的默认系统提示词
_COMPACTION_PROMPT = """
请将以下对话历史压缩为结构化摘要，保留关键信息。

输出格式（Markdown）：

## 目标
用户的核心需求和任务目标。

## 关键决策
已做出的重要决策和原因。

## 文件变更
已创建/修改的文件及其用途。

## 已完成
已经完成的步骤和结果。

## 待办
尚未完成的任务和下一步计划。

## 上下文笔记
其他需要 LLM 知道的上下文信息（如错误、约束条件等）。

要求：
- 只保留关键信息，省略冗余细节
- 保持简洁，摘要总长度不超过 500 字
- 如果已有旧摘要，请在其基础上增量更新"""


class CompactionResult:
    """压缩结果。

    Attributes:
        success: 是否成功。
        summary: 压缩摘要内容（成功时）。
        summary_tokens: 摘要的 token 数（成功时，由 estimate_tokens 估算）。
        compacted_count: 被压缩的消息数。
        retained_count: 保留的消息数。
        error: 失败时的错误信息。
    """

    def __init__(
        self,
        success: bool,
        summary: str = "",
        summary_tokens: int = 0,
        compacted_count: int = 0,
        retained_count: int = 0,
        error: str = "",
    ) -> None:
        self.success = success
        self.summary = summary
        self.summary_tokens = summary_tokens
        #: 被压缩的消息数
        self.compacted_count = compacted_count
        #: 保留的消息数
        self.retained_count = retained_count
        self.error = error


class CompactionManager:
    """上下文压缩管理器。
    管理上下文窗口的压缩决策和执行。

    Args:
        llm_client: LLM 客户端，用于生成摘要。
        hooks: 事件总线（可选），用于触发压缩事件。
        retained_tail: 保留尾部消息数（不被压缩的最近消息数）。
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        hooks: HookManager | None = None,
        retained_tail: int = 10,
    ) -> None:
        self._llm_client = llm_client
        self._hooks = hooks
        self._retained_tail = retained_tail

    def should_compact(
        self,
        messages: list[dict[str, Any]],
        context_usage: ContextUsage,
    ) -> bool:
        """判断是否应该触发压缩。

        - threshold: token 用量达到阈值
        - overflow: LLM 返回 length stop_reason（由调用方设置 is_overflow）
        - manual: 用户手动触发（直接调用 compact_session）

        Args:
            messages: 当前消息列表。
            context_usage: 上下文使用量监控器。

        Returns:
            是否应该压缩。
        """
        # 消息太少不压缩
        if len(messages) <= self._retained_tail + 2:
            return False

        # 达到阈值或溢出
        return context_usage.is_threshold_reached or context_usage.is_overflow

    def find_cut_point(
        self,
        messages: list[dict[str, Any]],
    ) -> int:
        """寻找安全切割点。

        从 retainedTail 边界向前寻找，避免切断 tool_call ↔ tool_result 配对。
        切割点必须在 tool_result 之后（确保 tool_call 和 tool_result 在同一段）。

        Args:
            messages: 消息列表。

        Returns:
            切割点索引（0 ~ cut），被压缩段为 messages[0:cut]，
            保留段为 messages[cut:]。
        """
        total = len(messages)
        # 理想切割点：total - retained_tail
        ideal_cut = max(1, total - self._retained_tail)

        # 向前寻找安全切割点（避免切断 tool_call ↔ tool_result）
        cut = ideal_cut
        while cut > 1:
            # 检查 cut 位置是否安全：messages[cut-1] 不应是 assistant（带 tool_calls）
            # 因为 assistant 的 tool_calls 需要紧跟着 tool 结果
            prev_msg = messages[cut - 1]
            prev_role = prev_msg.get("role", "")
            prev_has_tool_calls = bool(prev_msg.get("tool_calls"))

            if prev_role == "assistant" and prev_has_tool_calls:
                # assistant 带 tool_calls，需要把 tool_results 也包含在被压缩段
                # 向前移动切割点
                cut -= 1
                continue

            # 检查 cut 位置是否是 tool 结果的结尾
            # 如果 messages[cut] 是 tool，说明前面有 assistant(tool_calls) 被切断
            if cut < total and messages[cut].get("role") == "tool":
                # 向前找到 assistant(tool_calls) 之前
                cut -= 1
                continue

            # 安全切割点
            break

        return cut

    @staticmethod
    def _render_user_prompt(
        messages: list[dict[str, Any]],
        previous_summary: str,
    ) -> str:
        """构造压缩提示词中的用户片段，便于外部 prompt 覆盖时复用。"""
        prompt_content = "请压缩以下对话历史：\n\n"
        if previous_summary:
            prompt_content += f"[已有摘要]\n{previous_summary}\n\n[新增对话]\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            prompt_content += f"[{role}] {content}\n"
        return prompt_content

    def prepare_compaction(
        self,
        messages: list[dict[str, Any]],
        cut_point: int,
        previous_summary: str = "",
    ) -> list[dict[str, Any]]:
        """构建压缩消息。

        将被压缩段的消息提取出来，构造为 LLM 摘要请求的输入消息。

        Args:
            messages: 完整消息列表。
            cut_point: 切割点索引。
            previous_summary: 之前的摘要内容（迭代式摘要）。

        Returns:
            用于调用 LLM 的消息列表。
        """
        compacted = messages[:cut_point]
        return [
            {"role": "system", "content": _COMPACTION_PROMPT},
            {
                "role": "user",
                "content": self._render_user_prompt(compacted, previous_summary),
            },
        ]

    async def compact(
        self,
        messages: list[dict[str, Any]],
        previous_summary: str = "",
        prompt_override: str | None = None,
    ) -> str:
        """执行压缩，生成摘要。

        调用 LLM 将消息段压缩为结构化摘要，新摘要包含旧摘要内容。

        Args:
            messages: 被压缩的消息列表。
            previous_summary: 之前的摘要内容（迭代式摘要）。
            prompt_override: 压缩中间件提供的提示词覆盖；为 None 时使用默认提示词。

        Returns:
            压缩摘要文本。

        Raises:
            RuntimeError: LLM 客户端未设置或调用失败。
        """
        if self._llm_client is None:
            raise RuntimeError("压缩管理器未配置 LLM 客户端")

        if prompt_override:
            compaction_messages = [
                {"role": "system", "content": prompt_override},
                {
                    "role": "user",
                    "content": self._render_user_prompt(messages, previous_summary),
                },
            ]
        else:
            compaction_messages = self.prepare_compaction(
                messages,
                cut_point=len(messages),
                previous_summary=previous_summary,
            )

        response = await self._llm_client.complete(compaction_messages)
        return response.content

    async def compact_session(
        self,
        session: Session,
        context_usage: ContextUsage,
        force: bool = False,
    ) -> CompactionResult:
        """压缩会话上下文。

        完整的压缩流程：
        1. 判断是否需要压缩（force=True 时跳过检查）
        2. 寻找切割点
        3. 生成摘要
        4. 替换消息列表

        Args:
            session: 要压缩的会话。
            context_usage: 上下文使用量监控器。
            force: 是否强制压缩（手动触发时为 True）。

        Returns:
            CompactionResult: 压缩结果。
        """
        messages = session.to_messages()

        if not force and not self.should_compact(messages, context_usage):
            return CompactionResult(success=False, error="未达到压缩条件")

        if len(messages) <= self._retained_tail + 2:
            return CompactionResult(success=False, error="消息太少，无需压缩")

        # 寻找切割点
        cut = self.find_cut_point(messages)
        if cut < 1:
            return CompactionResult(success=False, error="无法找到安全切割点")

        # 提取被压缩段和保留段
        compacted = messages[:cut]
        retained = messages[cut:]

        # 查找已有摘要（迭代式摘要）
        previous_summary = ""
        for msg in session.messages:
            # 检查是否已有压缩摘要消息
            if hasattr(msg, "type") and getattr(msg, "type", None) == "compaction_summary":
                previous_summary = msg.content
                break

        # 触发 before_compact 事件：dispatch 同时支持取消 + 链式参数改写。
        prompt_override: str | None = None
        if self._hooks is not None:
            from pyagent.hooks.types import Event, EventType

            event = Event(
                type=EventType.SESSION_BEFORE_COMPACT,
                payload={
                    "session_id": session.metadata.id,
                    "compacted_count": cut,
                    "retained_count": len(retained),
                    "previous_summary": previous_summary,
                    "messages": compacted,
                },
            )
            # 初始值是当前默认参数 dict；handler 可返回 HookControl 取消或返回新 dict 改写。
            dispatch_result = await self._hooks.dispatch(
                event,
                initial={
                    "cut_point": cut,
                    "previous_summary": previous_summary,
                    "prompt": None,
                },
            )
            if dispatch_result.cancelled:
                return CompactionResult(
                    success=False,
                    error=dispatch_result.cancel_reason or "Hook 取消压缩",
                )

            overrides = dispatch_result.value or {}
            if overrides.get("cut_point") is not None:
                cut = overrides["cut_point"]
                compacted = messages[:cut]
                retained = messages[cut:]
            if overrides.get("previous_summary"):
                previous_summary = overrides["previous_summary"]
            prompt_override = overrides.get("prompt") or None

        # 生成摘要
        try:
            summary = await self.compact(
                compacted,
                previous_summary,
                prompt_override=prompt_override,
            )
        except Exception as exc:
            logger.exception("压缩摘要生成失败")
            return CompactionResult(
                success=False,
                error=f"摘要生成失败: {exc}",
                compacted_count=cut,
                retained_count=len(retained),
            )

        if not summary.strip():
            return CompactionResult(
                success=False,
                error="摘要为空",
                compacted_count=cut,
                retained_count=len(retained),
            )

        # 构造压缩摘要消息
        summary_tokens = estimate_tokens(summary)
        summary_msg = CompactionSummaryMessage(
            content=summary,
            compacted_range=f"0..{cut}",
            summary_tokens=summary_tokens,
        )

        # 重建会话消息列表：摘要 + 保留段
        # 保留段需要从原始 session.messages 中取（保留类型信息）
        retained_original = session.messages[cut:]
        session.messages = [summary_msg, *retained_original]

        # 更新元数据
        session.metadata.last_compaction_at = datetime.now(UTC)
        session.metadata.updated_at = datetime.now(UTC)

        # 基于压缩后的消息列表更新当前token使用量。
        post_messages: list[dict[str, Any]] = []
        if session.metadata.system_prompt:
            post_messages.append({"role": "system", "content": session.metadata.system_prompt})
        post_messages.extend(message_to_llm(m) for m in session.messages)
        context_usage.update(post_messages, last_usage=None)

        # 触发 compact 事件（纯通知，初始值 None 即可）
        if self._hooks is not None:
            from pyagent.hooks.types import Event, EventType

            await self._hooks.dispatch(
                Event(
                    type=EventType.SESSION_COMPACT,
                    payload={
                        "session_id": session.metadata.id,
                        "summary_tokens": summary_tokens,
                        "compacted_count": cut,
                        "retained_count": len(retained),
                    },
                )
            )

        logger.info(
            "会话 %s 压缩%d条消息完成: → 摘要 + 保留%d条消息",
            session.metadata.id,
            cut,
            len(retained),
        )

        return CompactionResult(
            success=True,
            summary=summary,
            summary_tokens=summary_tokens,
            compacted_count=cut,
            retained_count=len(retained),
        )

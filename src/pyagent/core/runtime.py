"""Runtime — 运行时环境。

Runtime 负责组装所有组件并管理生命周期：
- 创建 LLMClient、ToolRegistry、ToolExecutor、SkillManager、HookManager
- 自动发现并注册工具和技能
- 创建 Agent 和 AgentLoop
- 管理 Session

Runtime 是用户面对的入口，用户通过 Runtime 使用 Agent。

设计原则：
- Runtime 只管环境组装，不参与推理逻辑
- 推理逻辑在 AgentLoop 中
- Runtime → Agent → AgentLoop → (LLM, Tools, Skills)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pyagent.config.settings import Settings
from pyagent.core.agent import Agent
from pyagent.core.compaction import CompactionManager
from pyagent.core.context import RuntimeContext
from pyagent.core.loop import AgentLoop, LoopResult
from pyagent.hooks.manager import HookManager
from pyagent.hooks.types import Event, EventType
from pyagent.llm.client import LLMClient
from pyagent.session.store import SessionStore
from pyagent.session.types import Session
from pyagent.skills.discovery import SkillDiscovery
from pyagent.skills.manager import SkillManager
from pyagent.tools.discovery import ToolDiscovery
from pyagent.tools.executor import ToolExecutor
from pyagent.tools.registry import ToolRegistry
from pyagent.utils.discovery import DiscoveryItem
from pyagent.utils.filesystem import (
    get_project_skills_dir,
    get_user_skills_dir,
    get_user_tools_dir,
)
from pyagent.utils.logger import configure_file_logging, get_logger, set_log_level

logger = get_logger(__name__)


class Runtime:
    """运行时环境。

    用法::

        runtime = Runtime(settings)
        runtime.setup()  # 组装组件、发现工具和技能
        result = await runtime.run("帮我写个函数")
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.hooks = HookManager()
        self.llm_client: LLMClient | None = None
        self.tool_registry = ToolRegistry()
        self.tool_executor: ToolExecutor | None = None
        self.skill_manager = SkillManager()
        self.agent: Agent | None = None
        self.loop: AgentLoop | None = None
        self.session_store: SessionStore | None = None
        self._initialized = False

        # 当前运行时上下文引用
        self._current_ctx: RuntimeContext | None = None
        # 是否正在运行 Agent 循环
        self._is_running = False
        # DuplicateGuard 的 reset 闭包（per-Runtime，复用无需每次注册）
        self._duplicate_guard_reset: Callable[[], None] | None = None

    async def setup(self) -> None:
        """组装所有组件。

        按依赖顺序创建：
        1. HookManager（已创建）
        2. LLMClient
        3. ToolRegistry + ToolExecutor
        4. SkillManager
        5. SessionStore
        6. Agent
        7. AgentLoop
        """
        if self._initialized:
            return

        # 配置日志
        self._setup_logging()

        # 1. LLM 客户端
        llm_cfg = self.settings.llm
        self.llm_client = LLMClient(
            model=llm_cfg.model,
            api_key=llm_cfg.api_key,
            base_url=llm_cfg.base_url,
            timeout=llm_cfg.timeout,
        )

        # 2. 工具系统
        self.tool_executor = ToolExecutor(
            registry=self.tool_registry,
            hooks=self.hooks,
            mode=self.settings.agent.tool_execution,
        )

        # 自动发现工具
        self._discover_tools()

        # 3. 技能系统
        self._discover_skills()

        # 4. 会话存储
        if self.settings.session.enabled:
            session_dir = self.settings.session.dir
            if session_dir is None:
                # 默认 ~/.pyagent/sessions/
                from pyagent.utils.filesystem import get_user_sessions_dir

                session_dir = get_user_sessions_dir()
            self.session_store = SessionStore(Path(session_dir))

        # 5. Agent
        self.agent = Agent(
            llm_client=self.llm_client,
            tool_executor=self.tool_executor,
            skill_manager=self.skill_manager,
            hooks=self.hooks,
            system_prompt=self.settings.agent.system_prompt,
            max_turns=self.settings.agent.max_turns,
        )

        # 6. 上下文压缩管理器
        compaction_manager: CompactionManager | None = None
        if self.settings.agent.enable_compaction:
            compaction_manager = CompactionManager(
                llm_client=self.llm_client,
                hooks=self.hooks,
                retained_tail=self.settings.agent.compaction_retained_tail,
            )

        # 7. AgentLoop
        self.loop = AgentLoop(self.agent, compaction_manager=compaction_manager)

        # 8. 注册会话无关的内置 Hook（Logging / Permission / DuplicateGuard / Truncation）
        self._setup_builtin_hooks()

        self._initialized = True
        logger.info("Runtime 初始化完成")

    def _setup_logging(self) -> None:
        """配置日志级别和文件日志。"""
        from pyagent.utils.filesystem import get_user_logs_dir

        log_cfg = self.settings.log
        set_log_level(log_cfg.level)
        if log_cfg.file_enabled:
            log_dir = log_cfg.dir or get_user_logs_dir()
            configure_file_logging(
                log_dir=log_dir,
                filename=log_cfg.filename,
                level=log_cfg.level,
            )

    def _setup_builtin_hooks(self) -> None:
        """注册所有内置 Hook：Logging / Permission / Usage / TurnCount / AutoSave /
        DuplicateGuard / Truncation。

        所有 Hook 都在 ``setup()`` 一次性注册，运行期间不重复增册。
        需要访问当前 session 的 Hook（UsageTracking / TurnCounting / AutoSave）
        通过 ``self._current_session`` 延迟求值，每次事件触发时取最新实例。

        跨 session 的状态清理（如 DuplicateGuard 计数 reset）由
        ``Runtime.run`` 在每次开始时调用对应的 reset 闭包处理。

        受 ``Settings.hooks`` 控制:
            - ``hooks.enabled = False`` → 全部跳过
            - 单独的 enable_* / blocked_tools 决定细节
        """
        hook_cfg = self.settings.hooks
        if not hook_cfg.enabled:
            logger.debug("内置 Hook 已禁用 (Settings.hooks.enabled=False)")
            return

        from pyagent.hooks import (
            setup_logging_hooks,
            setup_permission_hooks,
        )

        if hook_cfg.enable_logging:
            setup_logging_hooks(self.hooks, logger)

        if hook_cfg.enable_permission and hook_cfg.blocked_tools:
            setup_permission_hooks(self.hooks, hook_cfg.blocked_tools)
            logger.info(
                "权限 Hook 已启用: 禁用工具 %s",
                sorted(hook_cfg.blocked_tools),
            )

        # ── 依赖当前 session 的 Hook（全部用 session_getter 延迟求值） ──
        from pyagent.hooks import (
            setup_auto_save_hook,
            setup_turn_counting_hook,
            setup_usage_tracking_hook,
        )

        if hook_cfg.enable_usage_tracking:
            setup_usage_tracking_hook(self.hooks, self._current_session, self.loop)
            logger.debug("Token 用量聚合 Hook 已启用")

        if hook_cfg.enable_turn_counting:
            setup_turn_counting_hook(self.hooks, self._current_session)
            logger.debug("轮次计数 Hook 已启用")

        if hook_cfg.enable_auto_save:
            setup_auto_save_hook(
                self.hooks,
                self.session_store,
                self._current_session,
            )
            logger.debug(
                "会话自动落盘 Hook 已启用 (session_store=%s)",
                "enabled" if self.session_store else "disabled",
            )

        # ── 会话无关的可立即注册的 Hook ──
        if hook_cfg.enable_duplicate_guard:
            from pyagent.hooks import setup_duplicate_tool_call_guard

            reset = setup_duplicate_tool_call_guard(
                self.hooks,
                threshold=hook_cfg.duplicate_guard_threshold,
            )
            self._duplicate_guard_reset = reset
            logger.debug(
                "重复工具调用守卫 Hook 已启用 (threshold=%d)",
                hook_cfg.duplicate_guard_threshold,
            )

        if hook_cfg.enable_result_truncation:
            from pyagent.hooks import setup_tool_result_truncation_hook

            setup_tool_result_truncation_hook(
                self.hooks,
                max_chars=hook_cfg.result_truncation_max_chars,
            )
            logger.debug(
                "工具结果截断 Hook 已启用 (max_chars=%d)",
                hook_cfg.result_truncation_max_chars,
            )

    def _discover_tools(self) -> None:
        """自动发现并注册工具。"""
        discovery = ToolDiscovery(self.tool_registry)

        # 内置工具
        builtin_dir = Path(__file__).parent.parent / "tools" / "builtin"
        if builtin_dir.exists():
            for py_file in builtin_dir.glob("*.py"):
                if py_file.name == "__init__.py" or py_file.stem.startswith("_"):
                    continue
                item = DiscoveryItem(
                    path=py_file,
                    name=py_file.stem,
                    source="builtin",
                )
                discovery.load(item)

        # 用户工具
        user_tools_dir = get_user_tools_dir()
        if user_tools_dir.exists():
            for py_file in user_tools_dir.glob("*.py"):
                if py_file.stem.startswith("_"):
                    continue
                item = DiscoveryItem(
                    path=py_file,
                    name=py_file.stem,
                    source="user",
                )
                discovery.load(item)

        logger.info("发现工具: %s", self.tool_registry.names())

    def _discover_skills(self) -> None:
        """自动发现并注册技能。

        借鉴 Pi Agent 的多源加载设计：
            builtin → user → project，同名保留首个。
        每个目录递归扫描，遇 SKILL.md 则加载该目录并停止递归。
        """
        discovery = SkillDiscovery()

        # 搜索目录列表（按优先级从低到高）
        search_dirs: list[tuple[Path, str]] = []

        # 内置技能
        builtin_dir = Path(__file__).parent.parent / "skills" / "builtin"
        if builtin_dir.exists():
            search_dirs.append((builtin_dir, "builtin"))

        # 用户技能
        user_skills_dir = get_user_skills_dir()
        if user_skills_dir.exists():
            search_dirs.append((user_skills_dir, "user"))

        # 项目技能
        project_skills_dir = get_project_skills_dir()
        if project_skills_dir.exists():
            search_dirs.append((project_skills_dir, "project"))

        # 扫描并加载
        skills, diagnostics = discovery.discover(search_dirs)

        # 注册到 manager（同名冲突由 manager.register 处理）
        for skill in skills:
            self.skill_manager.register(skill)

        # 记录诊断
        for diag in diagnostics:
            logger.warning("技能诊断 [%s]: %s (%s)", diag.code, diag.message, diag.path)

    async def run(
        self,
        query: str,
        session: Session | None = None,
        on_chunk: Any | None = None,
    ) -> LoopResult:
        """执行 Agent 循环。

        Args:
            query: 用户输入。
            session: 会话对象，不传则自动创建一个 ephemeral 会话
                （单次任务用完即弃，不写盘）。
            on_chunk: 流式回调。

        Returns:
            LoopResult: 循环结果。
        """
        if not self._initialized:
            await self.setup()
        assert self.loop is not None

        if session is None:
            session = self.create_session(mode="ephemeral")

        # Hook 已在 setup() 一次性注册，这里只需要在切换 session 时
        # reset 会话相关的状态（如 DuplicateGuard 跨任务计数）。
        if self._duplicate_guard_reset is not None:
            self._duplicate_guard_reset()

        # 创建ctx，供 steer/abort 在运行期间访问
        ctx = RuntimeContext(query=query, session=session)
        self._current_ctx = ctx
        self._is_running = True

        start_event = self._make_event("agent_start", query=query)
        start_result = await self.hooks.dispatch(start_event)
        if start_result.cancelled:
            self._is_running = False
            self._current_ctx = None
            return LoopResult(
                success=False,
                stop_reason="agent_cancelled",
                error=start_result.cancel_reason or "Agent 启动被 Hook 取消",
            )

        result: LoopResult | None = None
        try:
            result = await self.loop.run(query, session, on_chunk, ctx=ctx)
        finally:
            await self.hooks.dispatch(
                self._make_event(
                    "agent_end",
                    stop_reason=(result.stop_reason if result is not None else "error"),
                )
            )
            try:
                self.save_session(session)
            except Exception as exc:
                logger.warning("自动保存会话失败: %s", exc)
            self._is_running = False
            self._current_ctx = None

        return result

    def steer(self, text: str) -> bool:
        """在 Agent 运行期间注入改向输入。

        文本不立即注入消息历史，
        而是入队等待当前 turn 的所有工具调用完成后、下一轮 LLM 调用前
        统一注入，避免打断工具执行或破坏消息配对。

        Args:
            text: 用户补充输入。

        Returns:
            True 表示已入队，False 表示 Agent 未在运行。
        """
        if self._current_ctx is not None and self._is_running:
            self._current_ctx.steer(text)
            logger.info("改向输入已入队: %s", text[:50])
            return True
        return False

    def abort(self) -> bool:
        """中止当前 Agent 运行。

        通过设置 cancel_signal 通知循环和正在执行的工具。
        工具可监听 signal 提前终止（如 run_bash 会 kill 子进程）。

        Returns:
            True 表示已发送中止信号，False 表示 Agent 未在运行。
        """
        if self._current_ctx is not None and self._is_running:
            self._current_ctx.cancel()
            logger.info("已发送中止信号")
            return True
        return False

    @property
    def is_running(self) -> bool:
        """Agent 是否正在运行。"""
        return self._is_running

    def _current_session(self) -> Session | None:
        """返回当前运行 session，用于 AutoSaveHook 延迟求值。"""
        return self._current_ctx.session if self._current_ctx is not None else None

    def create_session(
        self,
        title: str = "",
        system_prompt: str = "",
        mode: str = "persistent",
    ) -> Session:
        """创建新会话。

        Args:
            title: 会话标题。
            system_prompt: 系统提示词（不传则用 settings）。
            mode: 持久化模式（"persistent" 或 "ephemeral"）。
                "ephemeral" 表示不写盘、单次任务用完即弃。
        """
        context_window = self.settings.agent.context_window
        compaction_threshold = self.settings.agent.compaction_threshold
        if self.session_store is None:
            # 无持久化存储时，创建内存会话（强制 ephemeral）
            return Session.create_new(
                session_id="ephemeral",
                model=self.settings.llm.model,
                system_prompt=system_prompt or self.settings.agent.system_prompt,
                title=title,
                context_window=context_window,
                compaction_threshold=compaction_threshold,
                mode="ephemeral",
            )

        return self.session_store.create(
            model=self.settings.llm.model,
            system_prompt=system_prompt or self.settings.agent.system_prompt,
            title=title,
            context_window=context_window,
            compaction_threshold=compaction_threshold,
            mode=mode,
        )

    def load_session(self, session_id: str) -> Session | None:
        """加载已有会话。"""
        if self.session_store is None:
            return None

        return self.session_store.load(session_id)

    def delete_session(self, session_id: str) -> bool:
        """删除指定会话。"""
        if self.session_store is None:
            return False

        return self.session_store.delete(session_id)

    def save_session(self, session: Session) -> None:
        """保存会话。"""
        if self.session_store is not None:
            self.session_store.save(session)

    def list_sessions(self) -> list:
        """列出所有会话。"""
        if self.session_store is None:
            return []

        return self.session_store.list_sessions()

    async def shutdown(self) -> None:
        """关闭运行时，释放资源。"""
        logger.info("Runtime 已关闭")

    @staticmethod
    def _make_event(event_type: str, **payload: Any) -> Event:
        """构造 Event 对象。"""
        return Event(type=EventType(event_type), payload=payload)

"""REPL — 交互式对话循环。

借鉴 Claude Code CLI 的交互风格：
- 简洁的欢迎界面，显示模型/工具/技能统计
- 用户输入用 > 前缀，不套 Panel
- Agent 回复流式逐字输出，Markdown 渲染
- 工具调用过程紧凑显示
- 命令系统：/help /exit /clear /sessions /tools /skills /info

打断机制：
- Agent 运行期间，独立 input task 持续读取 stdin
- 普通文本 → runtime.steer()，在 turn 边界注入
- /abort 或 Ctrl+C → runtime.abort()，中止当前运行
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from pyagent.cli.renderer import Renderer
from pyagent.utils.logger import get_logger

if TYPE_CHECKING:
    from pyagent.core.runtime import Runtime
    from pyagent.session.types import Session

logger = get_logger(__name__)


class REPL:
    """交互式 REPL。

    Args:
        runtime: 运行时环境。
        console: rich Console 实例。
    """

    def __init__(
        self,
        runtime: Runtime,
        console: Console | None = None,
    ) -> None:
        self.runtime = runtime
        self.console = console or Console()
        self.renderer = Renderer(self.console)
        self.session: Session | None = None
        self._running = False

        # ── stdin 读取基础设施 ──
        #: 唯一的 stdin reader task，确保同一时间只有一个 input() 在运行
        self._reader_task: asyncio.Task[None] | None = None
        #: 读取信号——set 时通知 reader 可以打印提示符并读 stdin
        self._read_signal: asyncio.Event = asyncio.Event()
        #: 输入队列——reader 读到的内容放入此处，消费者从中获取
        self._input_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def start(self) -> None:
        """启动 REPL 循环。"""
        self._running = True
        self._print_banner()

        # 创建新会话
        self.session = self.runtime.create_session(title="REPL 会话")

        # 启动唯一的 stdin reader task
        # 确保同一时间只有一个 input() 在阻塞读 stdin，
        # 避免 monitor cancel 后遗留的幽灵 input() 与下次读取竞争
        self._reader_task = asyncio.create_task(self._stdin_reader())

        while self._running:
            try:
                user_input = await self._read_input()
                if user_input is None:
                    break

                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    should_continue = await self._handle_command(user_input)
                    if not should_continue:
                        break
                    continue

                # 多行输入扩展：
                # - """...""" 块（首行以 """ 开头，持续读直到遇到下一行 """）
                # - 行末 \ 续行
                user_input = await self._expand_multiline(user_input)

                # 渲染用户输入
                self.renderer.user_input(user_input)

                # 执行 Agent
                await self._run_agent(user_input)

            except KeyboardInterrupt:
                self.console.print("\n  [dim]按 Ctrl+C 中断，输入 /exit 退出[/]")
            except EOFError:
                break
            except Exception as exc:
                logger.exception("REPL 异常")
                self.renderer.error(str(exc))

        # 清理 reader task
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        self._print_goodbye()

    async def _expand_multiline(self, first_line: str) -> str:
        r"""支持多行输入。

        触发条件：
        - 首行以 ``\"\"\"`` 开头：持续读直到下一行 ``\"\"\"`` 关闭
        - 首行（或任何续行）以 ``\\`` 结尾：持续读直到行末无 ``\\``

        Args:
            first_line: 已 strip 的首行输入。

        Returns:
            展开后的完整输入。
        """
        # 模式 1: """ ... """ 块
        if first_line.startswith('"""'):
            lines = [first_line]
            # 单行就关闭的情况:"""" 全部在同一行
            if first_line.count('"""') >= 2 and not first_line.endswith("\\"):
                return first_line
            while self._running:
                cont = await self._read_input()
                if cont is None:
                    break
                lines.append(cont)
                # 出现单独一行的 """ 表示结束
                stripped = cont.strip()
                if stripped == '"""' or (stripped.endswith('"""') and stripped.count('"""') == 1):
                    break
            return "\n".join(lines)

        # 模式 2: 行末 \ 续行
        if first_line.endswith("\\"):
            lines = [first_line[:-1]]  # 去掉行末 \
            while self._running:
                cont = await self._read_input()
                if cont is None:
                    break
                if cont.endswith("\\"):
                    lines.append(cont[:-1])
                    self.console.print("  [dim]…[/]", end="")
                else:
                    lines.append(cont)
                    break
            return "\n".join(lines)

        return first_line

    async def _stdin_reader(self) -> None:
        """唯一的 stdin 读取 task。

        通过 ``_read_signal`` + ``_input_queue`` 与消费者通信：
        - 等待 ``_read_signal`` 被 set（消费者调用 ``_read_input()`` 时触发）
        - 打印提示符，在 executor 中调用 ``input()``
        - 读到的内容放入 ``_input_queue`` 供消费者获取

        确保同一时间只有一个 ``input()`` 在阻塞读 stdin，
        避免 ``_monitor_input`` 被 cancel 后遗留的幽灵 ``input()``
        与主循环下次读取竞争 stdin（导致用户需要输入两次）。
        """
        loop = asyncio.get_event_loop()
        while self._running:
            # 等待读取信号
            await self._read_signal.wait()
            self._read_signal.clear()
            try:
                # 用 rich 渲染提示符（兼容 Windows 终端）
                self.console.print()
                self.console.print("[bold cyan]>[/] ", end="")
                result = await loop.run_in_executor(
                    None,
                    input,  # noqa: S603
                )
                await self._input_queue.put(result)
            except (EOFError, KeyboardInterrupt):
                await self._input_queue.put(None)
                self._running = False
                break

    async def _read_input(self) -> str | None:
        """读取用户输入。

        通过 set ``_read_signal`` 通知 ``_stdin_reader`` 打印提示符并读取，
        然后从 ``_input_queue`` 获取结果。
        """
        self._read_signal.set()
        return await self._input_queue.get()

    async def _run_agent(self, query: str) -> None:
        """执行 Agent 并流式渲染结果。

        运行期间启动独立 input task 监听 stdin：
        - 普通文本 → runtime.steer()，在 turn 边界注入
        - /abort 或 Ctrl+C → runtime.abort()，中止当前运行
        """
        from pyagent.cli.spinner import spinner

        try:
            collected: list[str] = []
            streaming_started = False

            def on_chunk(delta: str) -> None:
                nonlocal streaming_started
                if not streaming_started:
                    streaming_started = True
                    self.renderer.stream_start()
                collected.append(delta)
                self.renderer.stream_chunk(delta)

            # 启动 Agent 运行 task
            agent_task = asyncio.create_task(
                self.runtime.run(
                    query=query,
                    session=self.session,
                    on_chunk=on_chunk,
                )
            )

            # 启动独立 input 监控 task（运行期间持续读 stdin）
            input_task = asyncio.create_task(self._monitor_input(agent_task))

            try:
                with spinner(self.console, "思考中"):
                    result = await agent_task
            except KeyboardInterrupt:
                # Ctrl+C → abort
                self.runtime.abort()
                self.renderer.info("\n  [dim]已中止当前运行[/]")
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
                result = None
            finally:
                # monitor 用 agent_task.done() 主动退出，无需 cancel
                # 如果还在阻塞等 _read_input（用户没输入），取消并清掉残留的 queue
                if not input_task.done():
                    input_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await input_task
                # 清空可能残留的输入（用户在 agent 运行期间输入的文本）
                self._drain_input_queue()

            result = result if result is not None else agent_task.result() if agent_task.done() else None

            if streaming_started:
                self.renderer.stream_end()

            # 渲染结果
            if result is not None:
                if result.success:
                    if not streaming_started and result.final_response:
                        self.renderer.assistant_response(result.final_response)
                    elif not result.final_response and not streaming_started:
                        self.renderer.info("(无输出)")
                else:
                    if not streaming_started and result.final_response:
                        self.renderer.assistant_response(result.final_response)
                    self.renderer.info(f"停止原因: {result.stop_reason}")
                    if result.error:
                        self.renderer.error(result.error)

        except Exception as exc:
            self.renderer.error(f"执行失败: {exc}")

    async def _monitor_input(self, agent_task: asyncio.Task) -> None:
        """Agent 运行期间持续监听 stdin，实现 steer/abort。

        - 普通文本 → runtime.steer()，排队等 turn 边界注入
        - /abort → runtime.abort()，中止当前运行
        - Ctrl+C → 由外层 _run_agent 的 KeyboardInterrupt 捕获

        agent 完成后主动退出，不依赖 cancel（避免打断正在排队的 queue.get）。
        """
        while not agent_task.done():
            try:
                text = await self._read_input()
                if text is None:
                    continue
                text = text.strip()
                if not text:
                    continue

                if text in ("/abort", "/stop", "/cancel"):
                    ok = self.runtime.abort()
                    if ok:
                        self.renderer.info("  [dim]已发送中止信号[/]")
                    else:
                        self.renderer.info("  [dim]Agent 未在运行[/]")
                elif text.startswith("/"):
                    # 运行期间不处理其他命令
                    self.renderer.info("  [dim]Agent 运行中，请等待结束或输入 /abort 中止[/]")
                else:
                    # steer：排队等 turn 边界注入
                    ok = self.runtime.steer(text)
                    if ok:
                        preview = text[:40] + ("..." if len(text) > 40 else "")
                        self.renderer.info(f"  [dim]已排队改向: {preview}[/]")
                    else:
                        self.renderer.info("  [dim]Agent 未在运行[/]")
            except (EOFError, KeyboardInterrupt):
                # Ctrl+C → abort
                self.runtime.abort()
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("input monitor 异常: %s", exc)
                break

    def _drain_input_queue(self) -> None:
        """清空残留输入（agent 运行期间用户输入但未消费的文本）。"""
        while not self._input_queue.empty():
            try:
                self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _handle_command(self, cmd: str) -> bool:
        """处理 REPL 命令。

        Returns:
            True 继续循环，False 退出。
        """
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command in ("/exit", "/quit", "/q"):
            return False
        elif command == "/help":
            self._print_help()
        elif command == "/clear":
            self.session = self.runtime.create_session(title="REPL 会话")
            self.renderer.success("已清空对话，开始新会话。")
        elif command == "/sessions":
            self._list_sessions()
        elif command == "/session":
            if len(parts) < 2:
                self.renderer.info("用法: /session <id>")
            else:
                self._view_session(parts[1])
        elif command == "/resume":
            if len(parts) < 2:
                self.renderer.info("用法: /resume <id>")
            else:
                self._resume_session(parts[1])
        elif command == "/delete":
            if len(parts) < 2:
                self.renderer.info("用法: /delete <id>")
            else:
                self._delete_session(parts[1])
        elif command == "/tools":
            self._list_tools()
        elif command == "/skills":
            self._list_skills()
        elif command.startswith("/skill:"):
            await self._invoke_skill(command)
        elif command == "/info":
            self._show_info()
        elif command == "/context":
            self._show_context()
        elif command == "/compact":
            await self._manual_compact()
        elif command == "/abort":
            ok = self.runtime.abort()
            if ok:
                self.renderer.info("  [dim]已发送中止信号[/]")
            else:
                self.renderer.info("  [dim]Agent 未在运行[/]")
        else:
            self.renderer.info(f"未知命令: {command}，输入 /help 查看帮助。")

        return True

    def _print_banner(self) -> None:
        """打印欢迎横幅——Panel + 状态指标 + 快捷键提示。"""
        from pyagent import __version__

        tool_count = len(self.runtime.tool_registry.names())
        skill_count = len(self.runtime.skill_manager.names())
        model = self.runtime.settings.llm.model

        self.renderer.banner(
            title=f"PyAgent v{__version__}",
            subtitle="AI 编程助手 · 已就绪",
            stats=[
                ("模型", model),
                ("工具", str(tool_count)),
                ("技能", str(skill_count)),
            ],
        )
        self.console.print()
        self.renderer.hint("提示", "输入问题开始对话 · /help 查看命令 · /exit 退出")
        self.renderer.hint("运行中", "随时输入文本改向 (steer) · /abort 中止 · Ctrl+C 中断")
        self.renderer.hint("多行", '首尾用 """ 包裹或行末用 \\ 续行')
        self.console.print()

    def _print_help(self) -> None:
        """打印帮助信息——按类别分组的紧凑表格。"""
        groups: list[tuple[str, list[tuple[str, str]]]] = [
            (
                "对话",
                [
                    ("/help", "显示此帮助"),
                    ("/clear", "清空对话，开始新会话"),
                    ("/abort", "中止当前 Agent 运行"),
                    ("/exit", "退出 REPL（/quit, /q）"),
                ],
            ),
            (
                "会话",
                [
                    ("/sessions", "列出所有会话"),
                    ("/session <id>", "查看指定会话内容"),
                    ("/resume <id>", "恢复指定会话继续对话"),
                    ("/delete <id>", "删除指定会话"),
                ],
            ),
            (
                "工具与技能",
                [
                    ("/tools", "列出已加载的工具"),
                    ("/skills", "列出已加载的技能"),
                    ("/skill:<name>", "强制加载并执行指定技能"),
                ],
            ),
            (
                "上下文",
                [
                    ("/info", "显示运行时信息"),
                    ("/context", "显示上下文使用详情"),
                    ("/compact", "手动触发上下文压缩"),
                ],
            ),
        ]

        for group_name, rows in groups:
            table = Table(
                show_header=False,
                box=None,
                padding=(0, 2),
                title=f"[bold cyan]{group_name}[/]",
                title_justify="left",
            )
            table.add_column(style="bold cyan", no_wrap=True)
            table.add_column(style="white")
            for cmd, desc in rows:
                table.add_row(cmd, desc)
            self.console.print()
            self.console.print(table)

    def _list_sessions(self) -> None:
        """列出会话。"""
        sessions = self.runtime.list_sessions()
        if not sessions:
            self.renderer.info("暂无保存的会话。")
            return
        self.console.print()
        table = Table(title="会话列表", show_header=True, header_style="bold")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("标题", style="white")
        table.add_column("轮次", style="dim", justify="right")
        table.add_column("Tokens", style="dim", justify="right")
        for m in sessions:
            table.add_row(
                m.id,
                m.title or "(无标题)",
                str(m.turn_count),
                str(m.total_input_tokens + m.total_output_tokens),
            )
        self.console.print(table)

    def _view_session(self, session_id: str) -> None:
        """查看指定会话的对话内容。"""
        session = self.runtime.load_session(session_id)
        if session is None:
            self.renderer.error(f"会话 {session_id} 不存在。")
            return

        # 显示会话元数据
        self.console.print()
        meta = session.metadata
        self.console.print(f"  [bold]会话 {meta.id}[/]")
        self.console.print(
            f"  [dim]模型: {meta.model}  "
            f"轮次: {meta.turn_count}  "
            f"Tokens: {meta.total_input_tokens + meta.total_output_tokens}[/]"
        )
        self.console.print()

        # 显示对话消息
        for msg in session.messages:
            if msg.type == "system":
                continue
            if msg.type == "user":
                self.console.print(f"  [bold cyan]>[/] [cyan]{msg.content or ''}[/]")
            elif msg.type == "assistant":
                self.console.print(f"  [bold green]⬢[/] [green]{msg.content or ''}[/]")
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        self.console.print(f"     [yellow]⏺ {name}[/]")
            elif msg.type == "tool":
                content = msg.content or ""
                preview = content[:80] + "..." if len(content) > 80 else content
                self.console.print(f"     [dim]✓ {msg.name}: {preview}[/]")
            self.console.print()

    def _resume_session(self, session_id: str) -> None:
        """恢复指定会话，继续交互。"""
        session = self.runtime.load_session(session_id)
        if session is None:
            self.renderer.error(f"会话 {session_id} 不存在。")
            return

        self.session = session
        msg_count = len([m for m in session.messages if m.type != "system"])
        turn_count = session.metadata.turn_count
        self.renderer.success(f"已恢复会话 {session_id}（{msg_count} 条消息，{turn_count} 轮）。")

    def _delete_session(self, session_id: str) -> None:
        """删除指定会话。"""
        # 如果要删除的是当前会话，先确认
        if self.session and self.session.metadata.id == session_id:
            self.renderer.warning("正在删除当前会话，将创建新会话。")
            self.session = self.runtime.create_session(title="REPL 会话")

        ok = self.runtime.delete_session(session_id)
        if ok:
            self.renderer.success(f"已删除会话 {session_id}。")
        else:
            self.renderer.error(f"会话 {session_id} 不存在或删除失败。")

    def _list_tools(self) -> None:
        """列出工具。"""
        tools = self.runtime.tool_registry.all()
        if not tools:
            self.renderer.info("暂无已加载的工具。")
            return
        self.console.print()
        table = Table(title=f"已加载 {len(tools)} 个工具", show_header=True, header_style="bold")
        table.add_column("名称", style="yellow", no_wrap=True)
        table.add_column("描述", style="white")
        table.add_column("模式", style="dim")
        for tool in tools:
            table.add_row(tool.name, tool.description, tool.execution_mode)
        self.console.print(table)

    def _list_skills(self) -> None:
        """列出技能。"""
        skills = self.runtime.skill_manager.all()
        if not skills:
            self.renderer.info("暂无已加载的技能。")
            return
        self.console.print()
        table = Table(
            title=f"已加载 {len(skills)} 个技能",
            show_header=True,
            header_style="bold",
        )
        table.add_column("名称", style="magenta", no_wrap=True)
        table.add_column("描述", style="white")
        table.add_column("来源", style="dim")
        table.add_column("隐藏", style="dim", justify="center")
        for skill in skills:
            hidden = "是" if skill.disable_model_invocation else "—"
            table.add_row(skill.name, skill.description, skill.source, hidden)
        self.console.print(table)

    async def _invoke_skill(self, command: str) -> None:
        """处理 /skill:name 命令，强制加载并执行技能。

        用法：
            /skill:coding           加载 coding 技能
            /skill:coding 写个函数   加载技能并附加用户指令
        """
        # 解析 /skill:<name> [args...]
        rest = command[7:]  # 去掉 "/skill:"
        parts = rest.split(maxsplit=1)
        if not parts or not parts[0]:
            self.renderer.info("用法: /skill:<name> [附加指令]")
            return

        skill_name = parts[0]
        user_args = parts[1] if len(parts) > 1 else ""

        skill = self.runtime.skill_manager.get(skill_name)
        if skill is None:
            self.renderer.error(f"技能 '{skill_name}' 不存在。用 /skills 查看可用技能。")
            return

        # 生成调用块
        invocation = self.runtime.skill_manager.format_skill_invocation(skill_name, user_args)
        if invocation is None:
            self.renderer.error(f"技能 '{skill_name}' 调用失败。")
            return

        # 渲染用户输入
        display = f"/skill:{skill_name}"
        if user_args:
            display += f" {user_args}"
        self.renderer.user_input(display)

        # 把 invocation 作为 user message 执行 Agent
        await self._run_agent(invocation)

    def _show_info(self) -> None:
        """显示运行时信息——键值表形式。"""
        items: list[tuple[str, str]] = [
            ("模型", self.runtime.settings.llm.model),
            ("工具", f"{len(self.runtime.tool_registry.names())} 个"),
            ("技能", f"{len(self.runtime.skill_manager.names())} 个"),
            ("最大轮次", str(self.runtime.settings.agent.max_turns)),
        ]
        if self.session:
            items.extend(
                [
                    ("当前会话", self.session.metadata.id),
                    ("对话轮次", str(self.session.metadata.turn_count)),
                ]
            )
            total_tokens = self.session.metadata.total_input_tokens + self.session.metadata.total_output_tokens
            items.append(("Token 用量", str(total_tokens)))
        self.renderer.keyvalue(items, title="运行时信息")

    def _print_goodbye(self) -> None:
        """打印告别信息。"""
        self.console.print("\n  [bold green]再见！[/]\n")

    def _show_context(self) -> None:
        """显示上下文使用详情。

        显示：
        - 当前 token 使用量 / 窗口大小
        - 使用百分比和进度条
        - 压缩阈值
        - 消息数量
        """
        if not self.session:
            self.renderer.info("当前无活跃会话。")
            return

        # 延迟导入：避免 REPL 启动时加载 token_estimator
        from pyagent.llm.token_estimator import build_session_usage

        items: list[tuple[str, str]] = []

        # 消息数量
        msg_count = len(self.session.messages)
        items.append(("消息数量", str(msg_count)))

        # 累计 token 用量
        total_in = self.session.metadata.total_input_tokens
        total_out = self.session.metadata.total_output_tokens
        items.append(("累计输入 Tokens", str(total_in)))
        items.append(("累计输出 Tokens", str(total_out)))
        items.append(("累计总 Tokens", str(total_in + total_out)))

        # 上下文窗口配置
        ctx_window = self.session.metadata.context_window
        threshold = self.session.metadata.compaction_threshold
        if ctx_window > 0:
            items.append(("上下文窗口", str(ctx_window)))
            items.append(("压缩阈值", f"{int(threshold * 100)}% ({int(ctx_window * threshold)})"))

            # 复用 ContextUsage：统一估算 + 进度条
            usage = build_session_usage(self.session, default_limit=ctx_window)
            pct = usage.percentage * 100
            items.append(("当前上下文估算", f"{usage.used} ({pct:.1f}%)"))
            items.append(("使用进度", self.renderer.progress_bar(usage.used, ctx_window)))
        else:
            items.append(("上下文窗口", "[dim]未设置[/]"))

        # 压缩历史
        if self.session.metadata.last_compaction_at is not None:
            items.append(
                (
                    "上次压缩",
                    str(self.session.metadata.last_compaction_at.strftime("%H:%M:%S")),
                )
            )

        self.renderer.keyvalue(items, title="上下文使用详情")

    async def _manual_compact(self) -> None:
        """手动触发上下文压缩。"""
        if not self.session:
            self.renderer.info("当前无活跃会话。")
            return

        if self.runtime.loop is None or self.runtime.loop.compaction_manager is None:
            self.renderer.warning("上下文压缩未启用。请在配置中设置 enable_compaction=true。")
            return

        # 复用 build_session_usage：构造 + update 一步到位
        from pyagent.llm.token_estimator import build_session_usage

        context_usage = build_session_usage(self.session)

        self.renderer.info("正在压缩上下文...")

        result = await self.runtime.loop.compaction_manager.compact_session(
            self.session,
            context_usage,
            force=True,
        )

        if result.success:
            self.renderer.success(f"压缩完成: {result.compacted_count} 条消息 → 摘要 + {result.retained_count} 条保留")
        else:
            self.renderer.error(f"压缩失败: {result.error}")

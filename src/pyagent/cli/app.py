"""CLI 入口 — typer 命令行应用。

提供命令行接口：
    pyagent              启动交互式 REPL
    pyagent run <query>  单次执行查询
    pyagent tools        列出已加载工具
    pyagent skills       列出已加载技能
    pyagent sessions     列出保存的会话
    pyagent version      显示版本号

环境变量配置（前缀 PYAGENT_）：
    PYAGENT_LLM__MODEL       模型名
    PYAGENT_LLM__API_KEY     API Key
    PYAGENT_LLM__BASE_URL    自定义 API 地址
    PYAGENT_AGENT__MAX_TURNS 最大轮次
"""

from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console

from pyagent import __version__
from pyagent.cli.renderer import Renderer
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)

app = typer.Typer(
    name="pyagent",
    help="PyAgent — 开源 AI Agent 命令行工具",
    no_args_is_help=False,
    rich_markup_mode="rich",
    add_completion=False,
)

console = Console()
renderer = Renderer(console)


def _quiet_setup() -> Runtime:
    """加载配置并初始化 Runtime，静音 setup 阶段的 INFO 日志。

    子命令（chat/tools/skills/sessions）只关心结果，不需要看加载细节。
    使用 logging.disable 抑制所有 logger 的 INFO（比 setLevel 更稳，
    因为 _ensure_configured 会强制设 root logger 为 INFO）。
    """
    logging.disable(logging.INFO)
    try:
        settings = load_settings()
        runtime = Runtime(settings)
        runtime.setup()
    finally:
        logging.disable(logging.NOTSET)
    return runtime


@app.command()
def chat() -> None:
    """启动交互式 REPL 对话。"""
    runtime = _quiet_setup()

    from pyagent.cli.repl import REPL

    repl = REPL(runtime, console)
    try:
        asyncio.run(repl.start())
    except KeyboardInterrupt:
        console.print("\n  [dim]已退出[/]")


@app.command()
def run(
    query: str = typer.Argument(..., help="要执行的查询"),
    show_tools: bool = typer.Option(False, "--show-tools", "-t", help="显示工具调用过程"),
) -> None:
    """单次执行查询。"""
    runtime = _quiet_setup()

    result = asyncio.run(runtime.run(query))

    if result.final_response:
        renderer.assistant_response(result.final_response)

    if not result.success:
        renderer.info(f"停止原因: {result.stop_reason}")
        if result.error:
            renderer.error(result.error)

    renderer.info(f"轮次: {result.turns}")


@app.command()
def tools() -> None:
    """列出已加载的工具。"""
    runtime = _quiet_setup()

    all_tools = runtime.tool_registry.all()
    if not all_tools:
        renderer.info("暂无已加载的工具。")
        return

    items: list[tuple[str, str]] = []
    for tool in all_tools:
        items.append(
            (
                tool.name,
                f"{tool.description}  [dim]({tool.execution_mode})[/]",
            )
        )
    renderer.keyvalue(items, title=f"已加载 {len(all_tools)} 个工具")


@app.command()
def skills() -> None:
    """列出已加载的技能。"""
    runtime = _quiet_setup()

    all_skills = runtime.skill_manager.all()
    if not all_skills:
        renderer.info("暂无已加载的技能。")
        return

    items: list[tuple[str, str]] = []
    for skill in all_skills:
        hidden = "  [dim](隐藏)[/]" if skill.disable_model_invocation else ""
        items.append(
            (
                skill.name,
                f"{skill.description}  [dim]({skill.source})[/]{hidden}",
            )
        )
    renderer.keyvalue(items, title=f"已加载 {len(all_skills)} 个技能")


@app.command()
def sessions() -> None:
    """列出保存的会话。"""
    runtime = _quiet_setup()

    all_sessions = runtime.list_sessions()
    if not all_sessions:
        renderer.info("暂无保存的会话。")
        return

    items: list[tuple[str, str]] = []
    for m in all_sessions:
        total_tokens = m.total_input_tokens + m.total_output_tokens
        items.append(
            (
                f"[cyan]{m.id}[/]",
                f"{m.title or '(无标题)'}  [dim]({m.turn_count} 轮 · {total_tokens} tokens)[/]",
            )
        )
    renderer.keyvalue(items, title=f"共 {len(all_sessions)} 个会话")


@app.command()
def version() -> None:
    """显示版本号。"""
    console.print(f"[bold green]PyAgent[/] v{__version__}")


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
) -> None:
    """无子命令时启动 REPL。"""
    if ctx.invoked_subcommand is None:
        chat()


def main() -> None:
    """CLI 入口点（pyproject.toml [project.scripts] 指向这里）。"""
    app()


if __name__ == "__main__":
    main()

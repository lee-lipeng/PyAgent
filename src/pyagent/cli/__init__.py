"""CLI 命令行产品。

提供用户交互界面：
- app.py: typer CLI 入口（命令定义）
- repl.py: 交互式 REPL
- renderer.py: rich 终端渲染
- spinner.py: 加载动画
"""

from pyagent.cli.app import app, main

__all__ = ["app", "main"]

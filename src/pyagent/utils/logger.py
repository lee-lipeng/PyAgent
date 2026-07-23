"""日志配置。

提供统一的 get_logger 工厂，基于标准库 logging + rich 的 RichHandler，
开箱即用、彩色输出、不引入额外依赖。

支持同时输出到控制台和日志文件：
- 控制台：RichHandler，彩色输出
- 文件：RotatingFileHandler，保存到 ~/.pyagent/logs/pyagent.log
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# 全局标记，避免重复配置
_configured = False
# 保存配置后的日志目录路径，供外部查询
_log_dir: Path | None = None


def _ensure_configured() -> None:
    """首次调用时配置 root logger，只执行一次。"""
    global _configured, _log_dir
    if _configured:
        return

    from rich.logging import RichHandler

    # 控制台 handler
    console_handler = RichHandler(
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    console_handler.setLevel(logging.DEBUG)

    root = logging.getLogger("pyagent")
    root.setLevel(logging.INFO)
    root.addHandler(console_handler)
    root.propagate = False

    # 尝试添加文件 handler
    file_handler = _try_create_file_handler()
    if file_handler is not None:
        root.addHandler(file_handler)

    _configured = True


def _try_create_file_handler() -> RotatingFileHandler | None:
    """尝试创建文件日志 handler，失败时静默返回 None。"""
    global _log_dir
    try:
        from pyagent.utils.filesystem import get_user_logs_dir

        log_dir = get_user_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        _log_dir = log_dir

        log_file = log_dir / "pyagent.log"
        handler = RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,  # 2 MB
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        return handler
    except Exception:
        # 文件日志创建失败不影响核心功能
        return None


def configure_file_logging(
    log_dir: Path,
    filename: str = "pyagent.log",
    level: str = "DEBUG",
) -> None:
    """显式配置文件日志（供 Runtime 调用）。

    如果文件 handler 已存在则先移除旧的，再添加新的。
    """
    global _log_dir

    root = logging.getLogger("pyagent")

    # 移除已有的 FileHandler
    for h in root.handlers[:]:
        if isinstance(h, RotatingFileHandler):
            root.removeHandler(h)
            h.close()

    log_dir.mkdir(parents=True, exist_ok=True)
    _log_dir = log_dir

    log_file = log_dir / filename
    handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)


def set_log_level(level: str) -> None:
    """设置日志级别。"""
    root = logging.getLogger("pyagent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_log_dir() -> Path | None:
    """返回当前日志目录，未配置时返回 None。"""
    return _log_dir


def get_logger(name: str | None = None) -> logging.Logger:
    """获取一个 pyagent 命名空间下的 logger。

    Args:
        name: 子模块名，传 __name__ 即可。为 None 时返回根 logger。
    """
    _ensure_configured()
    if name is None:
        return logging.getLogger("pyagent")
    # 统一前缀，避免外部传 "pyagent.xxx" 产生重复
    if name.startswith("pyagent."):
        return logging.getLogger(name)
    return logging.getLogger(f"pyagent.{name}")

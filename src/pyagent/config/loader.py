"""多级配置加载器。

按优先级搜索配置文件，找到第一个存在的就加载。
搜索顺序（从低到高）：
    1. ~/.pyagent/settings.json  （用户全局）
    2. .pyagent/settings.json     （项目本地）
"""

from __future__ import annotations

from pathlib import Path

from pyagent.config.settings import Settings
from pyagent.utils.filesystem import get_project_config_dir, get_user_config_dir
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


def find_config_file(cwd: Path | None = None) -> Path | None:
    """按优先级搜索配置文件，返回第一个找到的路径。"""
    candidates = [
        get_user_config_dir() / "settings.json",
        get_project_config_dir(cwd) / "settings.json",
    ]
    for path in candidates:
        if path.exists():
            logger.debug("找到配置文件: %s", path)
            return path
    return None


def load_settings(cwd: Path | None = None) -> Settings:
    """加载配置，按多级优先级合并。

    Args:
        cwd: 当前工作目录，用于定位项目级配置。为 None 时用 Path.cwd()。
    """
    config_path = find_config_file(cwd)
    if config_path:
        logger.info("加载配置: %s", config_path)
        return Settings.load(config_path)
    logger.debug("未找到配置文件，使用默认配置")
    return Settings()

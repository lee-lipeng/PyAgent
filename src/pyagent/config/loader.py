"""多级配置加载器。

按优先级搜索配置文件，找到第一个存在的就加载。
搜索顺序（从低到高）：
    1. ~/.pyagent/settings.json  （用户全局）
    2. .pyagent/settings.json     （项目本地）
"""

from __future__ import annotations

import json
from pathlib import Path

from pyagent.config.settings import Settings, merge_settings_dicts
from pyagent.utils.filesystem import get_project_config_dir, get_user_config_dir
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


def find_config_file(cwd: Path | None = None) -> Path | None:
    """按优先级搜索配置文件，返回第一个找到的路径。"""
    candidates = [
        get_project_config_dir(cwd) / "settings.json",
        get_user_config_dir() / "settings.json",
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
    merged: dict = {}
    # 先加载全局，再加载项目配置；环境变量在 Settings.from_data 中最后覆盖。
    for path in (
        get_user_config_dir() / "settings.json",
        get_project_config_dir(cwd) / "settings.json",
    ):
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"配置文件不是 JSON 对象: {path}")
        merged = merge_settings_dicts(merged, data)
        logger.info("加载配置: %s", path)
    return Settings.from_data(merged)

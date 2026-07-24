"""importlib 动态加载工具。

核心能力：把一个 .py 文件路径动态导入为 Python 模块，
供 tools/discovery、hooks/builtin 等模块使用。
"""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from types import ModuleType

from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


def import_module_from_path(path: Path, module_name: str | None = None) -> ModuleType:
    """从文件路径动态导入 Python 模块。

    Args:
        path: .py 文件的绝对路径。
        module_name: 注册到 sys.modules 的模块名。为 None 时自动生成。

    Returns:
        导入后的 ModuleType 对象。

    Raises:
        ImportError: 文件不存在或导入失败时抛出。
    """
    path = path.resolve()
    if not path.exists():
        raise ImportError(f"模块文件不存在: {path}")

    if module_name is None:
        # 用文件路径生成唯一模块名，避免冲突
        module_name = f"_pyagent_dynamic_{abs(hash(str(path)))}"

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建模块 spec: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.error("动态导入模块失败: %s — %s", path, exc)
        raise ImportError(f"模块执行失败: {path}") from exc

    logger.debug("动态导入模块成功: %s → %s", path, module_name)
    return module

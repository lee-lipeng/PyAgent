"""ToolDiscovery — 自动扫描目录加载工具。

扫描指定目录下的 .py 文件，找到所有 @tool 装饰的类，
实例化并注册到 ToolRegistry。

扫描顺序：用户目录 → 项目目录（项目覆盖用户同名工具）。
"""

from __future__ import annotations

import hashlib
import inspect
from typing import TYPE_CHECKING

from pyagent.tools.base import Tool
from pyagent.tools.registry import ToolRegistry
from pyagent.utils import get_logger, import_module_from_path
from pyagent.utils.discovery import DiscoveryBase, DiscoveryItem, DiscoveryResult

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ToolDiscovery(DiscoveryBase):
    """工具自动发现器。

    Args:
        registry: 扫描到的工具会注册到这里。
    """

    file_pattern = "*.py"

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def load(self, item: DiscoveryItem) -> DiscoveryResult:
        """加载单个 .py 文件中的所有 @tool 装饰的类。"""
        # 跳过 __init__.py 和以 _ 开头的文件
        if item.path.name == "__init__.py" or item.path.stem.startswith("_"):
            return DiscoveryResult(loaded=False, skipped=True)

        # 不同目录可能包含同名文件；固定模块名会误用 sys.modules 中的旧文件。
        unique_name = hashlib.sha256(str(item.path.resolve()).encode("utf-8")).hexdigest()[:16]
        module_name = f"_pyagent_tool_{unique_name}"
        try:
            module = import_module_from_path(item.path, module_name)
        except Exception as exc:
            logger.warning("加载工具文件 %s 失败: %s", item.path, exc)
            return DiscoveryResult(loaded=False, skipped=True)

        # 扫描模块中所有 @tool 装饰的类
        loaded_count = 0
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            # 只处理定义在此模块中的类（排除 import 进来的）
            if obj.__module__ != module.__name__:
                continue
            # 检查是否有 _is_tool 标记
            if not getattr(obj, "_is_tool", False):
                continue
            # 确保是 Tool 子类
            if not issubclass(obj, Tool):
                continue

            try:
                instance = obj()
                self._registry.register(instance)
                loaded_count += 1
            except Exception as exc:
                logger.warning("实例化工具 %s 失败: %s", obj.__name__, exc)

        return DiscoveryResult(loaded=loaded_count > 0, skipped=False)

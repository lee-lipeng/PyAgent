"""通用发现基类。

tools/discovery、skills/discovery 的扫描逻辑高度相似——
都是"遍历目录 → 加载 → 注册"。这里抽出通用骨架，
子类只需实现 load() 方法。

设计要点：
- 按优先级顺序扫描多个目录，后扫描的同名项覆盖先扫描的
- 每个子目录独立处理，互不影响
- load() 由子类实现，返回是否成功
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DiscoveryItem:
    """发现到的一个条目。"""

    path: Path
    name: str
    source: str  # "builtin" | "user" | "project"


@dataclass
class DiscoveryResult:
    """一次发现扫描的结果汇总。"""

    loaded: list[DiscoveryItem] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)  # (path, reason)

    @property
    def total(self) -> int:
        return len(self.loaded)


class DiscoveryBase(ABC):
    """通用目录扫描发现基类。

    子类需实现：
        file_pattern: 要匹配的文件名模式（如 "*.py" 或 "SKILL.md"）
        load(item):  加载单个条目并注册到 registry
    """

    #: 子类覆盖：要匹配的文件名 glob 模式
    file_pattern: str = "*"

    def __init__(self, search_dirs: list[tuple[Path, str]]):
        """初始化发现器。

        Args:
            search_dirs: 按优先级从低到高排列的搜索目录列表，
                         每个元素是 (目录路径, 来源标签)。
                         后扫描的同名条目会覆盖先扫描的。
        """
        self._search_dirs = search_dirs

    def discover(self) -> DiscoveryResult:
        """执行扫描，返回结果汇总。"""
        result = DiscoveryResult()
        # name → item，后扫描的覆盖先扫描的
        seen: dict[str, DiscoveryItem] = {}

        for dir_path, source in self._search_dirs:
            if not dir_path.exists():
                continue
            for item in self._scan_dir(dir_path, source):
                if item.name in seen:
                    logger.debug(
                        "覆盖同名条目: %s（%s → %s）",
                        item.name,
                        seen[item.name].source,
                        source,
                    )
                seen[item.name] = item

        for item in seen.values():
            try:
                if self.load(item):
                    result.loaded.append(item)
                else:
                    result.skipped.append((item.path, "load() 返回 False"))
            except Exception as exc:
                logger.warning("加载条目失败: %s — %s", item.path, exc)
                result.skipped.append((item.path, str(exc)))

        logger.info("发现完成: 加载 %d 个，跳过 %d 个", result.total, len(result.skipped))
        return result

    def _scan_dir(self, dir_path: Path, source: str) -> list[DiscoveryItem]:
        """扫描单个目录，返回匹配的条目列表。"""
        items: list[DiscoveryItem] = []
        for path in sorted(dir_path.glob(self.file_pattern)):
            if path.is_dir():
                continue
            name = self._extract_name(path)
            if name is None:
                continue
            items.append(DiscoveryItem(path=path, name=name, source=source))
        return items

    def _extract_name(self, path: Path) -> str | None:
        """从文件路径提取条目名。默认用文件名（不含扩展名）。

        子类可覆盖此方法实现不同的命名逻辑。
        """
        return path.stem

    @abstractmethod
    def load(self, item: DiscoveryItem) -> bool:
        """加载单个条目并注册到 registry。

        Args:
            item: 发现到的条目信息。

        Returns:
            True 表示加载成功，False 表示跳过。
        """
        ...

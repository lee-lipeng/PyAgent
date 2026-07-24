"""SkillDiscovery — 自动扫描目录加载技能。

- 多源加载：builtin → user → project → settings → cli
- 递归扫描：遇到 SKILL.md 则加载该目录并停止向下递归
- 根 .md 文件：在 builtin/user/project 层可作独立 skill
- 忽略文件：尊重 .gitignore / .ignore
- 同名冲突：保留首个，记录诊断
- 返回 (skills, diagnostics) 而非静默跳过
"""

from __future__ import annotations

from pathlib import Path

from pyagent.skills.loader import SkillLoader
from pyagent.skills.types import Skill, SkillDiagnostic
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)

# skill 入口文件名
SKILL_FILE_NAME = "SKILL.md"

# 忽略文件名列表
_IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".fdignore")

# 不递归进入的目录名
_SKIP_DIR_NAMES = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv"})


class SkillDiscovery:
    """技能自动发现器。

    用法::

        discovery = SkillDiscovery()
        skills, diagnostics = discovery.discover([
            (builtin_dir, "builtin"),
            (user_dir, "user"),
            (project_dir, "project"),
        ])
    """

    def discover(
        self,
        search_dirs: list[tuple[Path, str]],
        *,
        allow_root_md: bool = True,
    ) -> tuple[list[Skill], list[SkillDiagnostic]]:
        """扫描多个目录，返回技能列表和诊断列表。

        Args:
            search_dirs: 按优先级从低到高排列的 (目录, 来源) 列表。
                后扫描的同名 skill 会覆盖先扫描的（与 Pi 一致：保留首个）。
            allow_root_md: 是否将目录根下的 .md 文件作为独立 skill。
                builtin/user/project 层允许，其他层不允许。

        Returns:
            (skills, diagnostics)。
        """
        all_skills: list[Skill] = []
        all_diagnostics: list[SkillDiagnostic] = []
        # name → skill 映射，用于检测同名冲突
        seen: dict[str, Skill] = {}

        for dir_path, source in search_dirs:
            if not dir_path.exists():
                continue
            if not dir_path.is_dir():
                continue

            skills, diags = self._scan_dir(dir_path, source, allow_root_md=allow_root_md)
            all_diagnostics.extend(diags)

            for skill in skills:
                if skill.name in seen:
                    # 同名保留首个，记录 warning
                    all_diagnostics.append(
                        SkillDiagnostic(
                            code="name_collision",
                            message=(
                                f"技能 '{skill.name}' 已存在于 "
                                f"{seen[skill.name].source}，"
                                f"来自 {source} 的同名技能被跳过"
                            ),
                            path=skill.file_path,
                        )
                    )
                    logger.warning(f"技能同名冲突: {skill.name}（保留 {seen[skill.name].source}，跳过{source}）")
                    continue
                seen[skill.name] = skill
                all_skills.append(skill)

        logger.info(f"发现技能: {[s.name for s in all_skills]}")
        return all_skills, all_diagnostics

    def _scan_dir(
        self,
        dir_path: Path,
        source: str,
        *,
        allow_root_md: bool,
        ignore_patterns: set[str] | None = None,
        is_root: bool = True,
    ) -> tuple[list[Skill], list[SkillDiagnostic]]:
        """递归扫描单个目录。

        规则：
            - 遇到 SKILL.md → 加载该目录为 skill，不再向下递归
            - 否则递归子目录
            - 根 .md 文件可作 skill（仅当 allow_root_md 且 is_root）
            - 尊重 .gitignore / .ignore
        """
        skills: list[Skill] = []
        diagnostics: list[SkillDiagnostic] = []

        # 读取忽略文件
        patterns = ignore_patterns or set()
        patterns |= self._read_ignore_file(dir_path)

        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: e.name)
        except OSError as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code="list_failed",
                    message=str(exc),
                    path=dir_path,
                )
            )
            return skills, diagnostics

        # 第一轮：查找 SKILL.md
        for entry in entries:
            if entry.name == SKILL_FILE_NAME and entry.is_file():
                skill, diags = SkillLoader.load(entry, source=source)
                diagnostics.extend(diags)
                if skill is not None:
                    skills.append(skill)
                # 找到 SKILL.md 后不再递归该目录
                return skills, diagnostics

        # 第二轮：递归子目录 + 根 .md 文件
        for entry in entries:
            # 跳过点开头文件/目录和已知无关目录
            if entry.name.startswith(".") or entry.name in _SKIP_DIR_NAMES:
                continue

            # 检查是否被忽略
            if self._is_ignored(entry.name, patterns):
                continue

            if entry.is_dir():
                sub_skills, sub_diags = self._scan_dir(
                    entry,
                    source,
                    allow_root_md=allow_root_md,
                    ignore_patterns=patterns,
                    is_root=False,
                )
                skills.extend(sub_skills)
                diagnostics.extend(sub_diags)
            elif entry.is_file() and entry.suffix == ".md" and allow_root_md and is_root:
                # 根 .md 文件作为独立 skill
                skill, diags = SkillLoader.load(entry, source=source)
                diagnostics.extend(diags)
                if skill is not None:
                    skills.append(skill)

        return skills, diagnostics

    def _read_ignore_file(self, dir_path: Path) -> set[str]:
        """读取目录下的忽略文件，返回忽略模式集合。"""
        patterns: set[str] = set()
        for name in _IGNORE_FILE_NAMES:
            ignore_file = dir_path / name
            if not ignore_file.is_file():
                continue
            try:
                content = ignore_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                patterns.add(stripped)
        return patterns

    def _is_ignored(self, name: str, patterns: set[str]) -> bool:
        """简单忽略匹配（支持精确匹配、目录模式和通配符）。

        注意：这是简化版的 gitignore 匹配，不做完整 gitignore 语义。
        对于大多数 skill 目录场景足够。

        支持的模式：
            - ``file``       精确匹配文件/目录名
            - ``dir/``       匹配目录（尾部斜杠被忽略）
            - ``*.tmp``      通配符匹配
        """
        for pattern in patterns:
            # 去掉尾部斜杠，统一按名称匹配
            pat = pattern.rstrip("/")
            if pat == name:
                return True
            if "*" in pat or "?" in pat:
                import fnmatch

                if fnmatch.fnmatch(name, pat):
                    return True
        return False

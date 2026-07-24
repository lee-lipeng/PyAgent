"""Skill 系统：技能加载、注册、发现。
参考 https://agentskills.io/specification设计Skills

- Skill 用目录组织，每个目录一个 SKILL.md（含 frontmatter 元数据）
- frontmatter 必填 name + description，对齐 Agent Skills 规范
- system prompt 只注入 name/description/file_path（渐进式披露）
- LLM 读 description 自行判断是否加载完整正文
- SkillManager 管理技能注册表，提供 system prompt 渲染和 /skill:name 调用
- SkillDiscovery 递归扫描多源目录，支持 .gitignore
"""

from pyagent.skills.discovery import SkillDiscovery
from pyagent.skills.loader import SkillLoader
from pyagent.skills.manager import SkillManager
from pyagent.skills.types import (
    Skill,
    SkillDiagnostic,
    SkillFrontmatter,
    validate_description,
    validate_name,
)

__all__ = [
    "Skill",
    "SkillDiagnostic",
    "SkillDiscovery",
    "SkillFrontmatter",
    "SkillLoader",
    "SkillManager",
    "validate_description",
    "validate_name",
]

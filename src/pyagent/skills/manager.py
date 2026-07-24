"""SkillManager — 技能注册表和 system prompt 渲染。

借鉴 Pi Agent 的设计：

- system prompt 只注入 name/description/file_path（XML 块），渐进式披露
- 同名注册产生 warning，保留首个
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from pyagent.skills.types import Skill, SkillDiagnostic
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


class SkillManager:
    """技能管理器。

    用法::

        manager = SkillManager()
        manager.register(skill)
        # 注入 system prompt（仅 name/description/file_path）
        block = manager.format_for_system_prompt()
        # /skill:name 强制加载完整正文
        invocation = manager.format_skill_invocation("coding", "写个函数")
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> SkillDiagnostic | None:
        """注册一个技能。

        同名技能保留首个，返回 collision 诊断。

        Returns:
            若发生同名冲突，返回 SkillDiagnostic；否则 None。
        """
        name = skill.name
        if name in self._skills:
            existing = self._skills[name]
            return SkillDiagnostic(
                code="name_collision",
                message=f"技能 '{name}' 已存在于 {existing.source}，来自 {skill.source} 的同名技能被跳过",
                path=skill.file_path,
            )
        self._skills[name] = skill
        logger.debug(f"注册技能: {name} (来源: {skill.source})")
        return None

    def unregister(self, name: str) -> None:
        """移除已注册的技能。"""
        self._skills.pop(name, None)

    def get(self, name: str) -> Skill | None:
        """按名称获取技能。"""
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        """返回所有已注册的技能列表。"""
        return list(self._skills.values())

    def names(self) -> list[str]:
        """返回所有已注册的技能名列表。"""
        return list(self._skills.keys())

    def clear(self) -> None:
        """清空注册表。"""
        self._skills.clear()

    def format_for_system_prompt(self) -> str:
        """生成 <available_skills> XML 块，注入 system prompt。

        遵循 Agent Skills 规范（agentskills.io/integrate-skills）：
        只列出 name/description/location，不包含完整正文。
        LLM 看到匹配的 description 后自行用 read_file 加载 SKILL.md。

        过滤掉 disable_model_invocation=True 的技能。
        """
        visible = [s for s in self._skills.values() if not s.disable_model_invocation]
        if not visible:
            return ""

        lines = [
            "以下技能为特定任务提供专门指令。",
            "当任务匹配某技能的描述时，用 read_file 工具加载完整 SKILL.md。",
            "技能文件中的相对路径以 SKILL.md 所在目录为基准解析。",
            "",
            "<available_skills>",
        ]

        for skill in visible:
            lines.append("  <skill>")
            lines.append(f"    <name>{escape(skill.name)}</name>")
            lines.append(f"    <description>{escape(skill.description)}</description>")
            lines.append(f"    <location>{escape(skill.file_path_str)}</location>")
            lines.append("  </skill>")

        lines.append("</available_skills>")
        return "\n".join(lines)

    def format_skill_invocation(
        self,
        name: str,
        user_args: str = "",
    ) -> str | None:
        """生成 <skill> 调用块，供 /skill:name 命令使用。

        把完整 body 注入上下文，附加用户额外指令。
        即使 disable_model_invocation=True 的技能也可通过此方式调用。

        Args:
            name: 技能名。
            user_args: 用户附加指令（/skill:name 后面的参数）。

        Returns:
            调用块文本，技能不存在则返回 None。
        """
        skill = self._skills.get(name)
        if skill is None:
            return None

        # 构造 <skill> 块，告知 LLM 相对路径基准
        block = (
            f'<skill name="{escape(skill.name)}" '
            f'location="{escape(skill.file_path_str)}">\n'
            f"相对路径以 {escape(skill.file_path_str)} 所在目录为基准。\n\n"
            f"{skill.body}\n"
            f"</skill>"
        )
        if user_args:
            block += f"\n\nUser: {user_args}"
        return block

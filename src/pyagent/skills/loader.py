"""SkillLoader — 从 SKILL.md 文件加载技能。

SKILL.md 格式（对齐 Agent Skills 规范）::

    ---
    name: coding
    description: 代码编写与调试技能，指导 Agent 如何编写、编辑和调试代码
    disable_model_invocation: false
    ---

    # 代码编写技能

    当用户要求编写、修改或调试代码时，遵循以下原则...

设计要点：
- 解析失败返回诊断信息而非静默 None
- name 不合规产生 warning 但仍加载
- description 为空是唯一硬错误（不加载）
- 未知 frontmatter 字段被忽略
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from pyagent.skills.types import (
    Skill,
    SkillDiagnostic,
    SkillFrontmatter,
    validate_description,
    validate_name,
)
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)

# frontmatter 正则：--- 开头和结尾的 YAML 块
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class SkillLoader:
    """技能文件加载器。

    - 返回 (Skill | None, list[SkillDiagnostic])
    - name 校验不合规产生 warning 但仍加载
    - description 为空直接不加载（硬错误）
    """

    @staticmethod
    def load(
        path: Path,
        source: str = "unknown",
    ) -> tuple[Skill | None, list[SkillDiagnostic]]:
        """从 SKILL.md 文件加载技能。

        Args:
            path: SKILL.md 文件路径。
            source: 来源标记（builtin/user/project/cli/settings）。

        Returns:
            (Skill 对象或 None, 诊断列表)。
            加载成功时诊断列表可能含 warning（如 name 不合规）。
            加载失败时返回 (None, diagnostics)。
        """
        diagnostics: list[SkillDiagnostic] = []

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"读取技能文件失败 {path}: {exc}")
            diagnostics.append(
                SkillDiagnostic(
                    code="read_failed",
                    message=str(exc),
                    path=path,
                )
            )
            return None, diagnostics

        match = _FRONTMATTER_RE.match(content)
        if match:
            yaml_text = match.group(1)
            body = match.group(2).strip()
            try:
                data = yaml.safe_load(yaml_text)
                if not isinstance(data, dict):
                    diagnostics.append(
                        SkillDiagnostic(
                            code="parse_failed",
                            message="frontmatter 不是字典",
                            path=path,
                        )
                    )
                    return None, diagnostics
            except yaml.YAMLError as exc:
                diagnostics.append(
                    SkillDiagnostic(
                        code="parse_failed",
                        message=f"YAML 解析失败: {exc}",
                        path=path,
                    )
                )
                return None, diagnostics
        else:
            # 没有 frontmatter，整个文件作为 body
            body = content.strip()
            data = {}

        frontmatter_name = data.get("name")
        parent_dir_name = path.parent.name
        name = frontmatter_name if isinstance(frontmatter_name, str) else parent_dir_name

        # name 校验（warning，不阻断）
        for error in validate_name(name):
            diagnostics.append(
                SkillDiagnostic(
                    code="invalid_metadata",
                    message=error,
                    path=path,
                )
            )

        description = data.get("description")
        desc = description if isinstance(description, str) else ""

        desc_errors = validate_description(desc)
        if desc_errors:
            for error in desc_errors:
                diagnostics.append(
                    SkillDiagnostic(
                        code="invalid_metadata",
                        message=error,
                        path=path,
                    )
                )
            # description 为空 → 不加载
            return None, diagnostics

        try:
            frontmatter = SkillFrontmatter(
                name=name,
                description=desc,
                disable_model_invocation=data.get("disable-model-invocation", False),
                license=data.get("license"),
                compatibility=data.get("compatibility"),
                metadata=data.get("metadata", {}),
                allowed_tools=data.get("allowed-tools"),
            )
        except Exception as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code="invalid_metadata",
                    message=f"frontmatter 构建失败: {exc}",
                    path=path,
                )
            )
            return None, diagnostics

        skill = Skill(
            frontmatter=frontmatter,
            body=body,
            file_path=path.resolve(),
            base_dir=path.parent.resolve(),
            source=source,
        )

        if diagnostics:
            logger.warning(
                "技能 %s 加载成功但有 %d 个警告: %s",
                name,
                len(diagnostics),
                "; ".join(d.message for d in diagnostics),
            )
        else:
            logger.debug(f"加载技能: {name} (来源: {source})")

        return skill, diagnostics

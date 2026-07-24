"""Skill 数据模型。

- Skill 由 frontmatter（元数据）和 body（指令正文）组成
- frontmatter 用 YAML 格式，必填 name + description
- system prompt 只注入 name/description/file_path（渐进式披露）
- body 由 LLM 按需用 read_file 加载
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# skill name 最大长度
MAX_NAME_LENGTH = 64

# skill description 最大长度
MAX_DESCRIPTION_LENGTH = 1024

# 合法 name 字符集：小写字母、数字、连字符
_NAME_RE = re.compile(r"^[a-z0-9-]+$")


class SkillDiagnostic(BaseModel):
    """技能加载诊断信息。

    加载失败不阻断整体流程，而是返回诊断列表供上层展示。

    Attributes:
        code: 稳定诊断码（parse_failed / invalid_metadata / read_failed ...）。
        message: 人类可读的诊断描述。
        path: 关联文件路径。
    """

    code: str
    message: str
    path: Path


class SkillFrontmatter(BaseModel):
    """技能 frontmatter 元数据。

    Attributes:
        name: 技能名，小写字母+数字+连字符，≤64 字符。
        description: 技能描述，≤1024 字符。决定 LLM 何时加载此技能。
        disable_model_invocation: 为 True 时从 system prompt 隐藏，
            仅 /skill:name 可显式调用。
        license: 许可证名称或引用。
        compatibility: 环境兼容性要求（≤500 字符）。
        metadata: 任意键值对，不做解释。
        allowed_tools: 预批准工具列表（experimental）。
    """

    name: str = Field(description="技能名")
    description: str = Field(default="", description="技能描述")
    disable_model_invocation: bool = Field(default=False, description="隐藏于 system prompt，仅 /skill:name 可用")
    license: str | None = Field(default=None, description="许可证")
    compatibility: str | None = Field(default=None, description="环境兼容性要求")
    metadata: dict[str, Any] = Field(default_factory=dict, description="任意元数据")
    allowed_tools: list[str] | None = Field(default=None, description="预批准工具列表")


class Skill(BaseModel):
    """技能实体。

    Attributes:
        frontmatter: 元数据。
        body: 指令正文（Markdown）。
        file_path: SKILL.md 绝对路径，用于 LLM read_file。
        base_dir: SKILL.md 所在目录，相对路径解析基准。
        source: 来源（builtin / user / project / cli / settings）。
    """

    frontmatter: SkillFrontmatter
    body: str
    file_path: Path
    base_dir: Path
    source: str = "unknown"

    @property
    def name(self) -> str:
        """技能名。"""
        return self.frontmatter.name

    @property
    def description(self) -> str:
        """技能描述。"""
        return self.frontmatter.description

    @property
    def disable_model_invocation(self) -> bool:
        """是否对 LLM 隐藏。"""
        return self.frontmatter.disable_model_invocation

    @property
    def file_path_str(self) -> str:
        """file_path 的字符串形式（用于 prompt 注入，正斜杠风格）。"""
        return str(self.file_path).replace("\\", "/")


def validate_name(name: str) -> list[str]:
    """校验 skill name，返回错误信息列表（空列表表示通过）。

    规则：
        - ≤64 字符
        - 仅小写字母、数字、连字符
        - 不能首尾连字符
        - 不能连续连字符
    """
    errors: list[str] = []
    if len(name) > MAX_NAME_LENGTH:
        errors.append(f"name 超过 {MAX_NAME_LENGTH} 字符（{len(name)}）")
    if not _NAME_RE.match(name):
        errors.append("name 含非法字符（仅允许小写 a-z、0-9、连字符）")
    if name.startswith("-") or name.endswith("-"):
        errors.append("name 不能以连字符开头或结尾")
    if "--" in name:
        errors.append("name 不能包含连续连字符")
    return errors


def validate_description(description: str | None) -> list[str]:
    """校验 skill description，返回错误信息列表。

    规则：
        - 不能为空（空则不加载，是唯一硬错误）
        - ≤1024 字符
    """
    errors: list[str] = []
    if not description or not description.strip():
        errors.append("description 不能为空")

    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"description 超过 {MAX_DESCRIPTION_LENGTH} 字符（{len(description)}）")

    return errors

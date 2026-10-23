"""项目 Skill 优先于用户和内置，frontmatter 兼容两种隐藏字段。"""

from pyagent.config.settings import Settings
from pyagent.core.runtime import Runtime
from pyagent.skills.loader import SkillLoader


def test_project_skill_takes_priority_over_builtin(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    target = project_dir / "coding"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: coding\ndescription: project coding skill\n---\ncustom",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pyagent.core.runtime.get_project_skills_dir", lambda: project_dir
    )
    monkeypatch.setattr(
        "pyagent.core.runtime.get_user_skills_dir", lambda: tmp_path / "empty"
    )
    runtime = Runtime(Settings())
    runtime._discover_skills()
    skill = runtime.skill_manager.get("coding")
    assert skill is not None
    assert skill.source == "project"
    assert skill.body == "custom"


def test_skill_hidden_field_underscore_alias(tmp_path):
    folder = tmp_path / "my-skill"
    folder.mkdir()
    file = folder / "SKILL.md"
    file.write_text(
        "---\nname: my-skill\ndescription: testing\ndisable_model_invocation: true\n"
        "---\nbody", encoding="utf-8"
    )
    skill, diagnostics = SkillLoader.load(file)
    assert skill is not None
    assert skill.disable_model_invocation is True

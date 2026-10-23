"""默认、全局文件、项目文件、环境变量逐层覆盖。"""

import json

from pyagent.config.loader import load_settings
from pyagent.config.settings import Settings


def test_project_overrides_user_and_environment_overrides_project(tmp_path, monkeypatch):
    user = tmp_path / "user"
    project = tmp_path / "project"
    user.mkdir()
    project.mkdir()
    (user / "settings.json").write_text(
        json.dumps({"llm": {"model": "from-user", "temperature": 0.2},
                    "agent": {"max_turns": 11}}),
        encoding="utf-8",
    )
    (project / "settings.json").write_text(
        json.dumps({"llm": {"model": "from-project"}, "agent": {"max_turns": 22}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("pyagent.config.loader.get_user_config_dir", lambda: user)
    monkeypatch.setattr("pyagent.config.loader.get_project_config_dir", lambda cwd: project)
    monkeypatch.setenv("PYAGENT_LLM__MODEL", "from-env")

    settings = load_settings()
    assert settings.llm.model == "from-env"
    assert settings.llm.temperature == 0.2
    assert settings.agent.max_turns == 22


def test_settings_load_respects_nested_environment(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"llm": {"model": "from-file", "temperature": 0.2}}')
    monkeypatch.setenv("PYAGENT_LLM__MODEL", "from-env")
    loaded = Settings.load(path)
    assert loaded.llm.model == "from-env"
    assert loaded.llm.temperature == 0.2

"""文件系统工具函数。

集中处理路径查找、多级目录搜索等通用逻辑，
供 tools/discovery、skills/discovery、config/loader 等模块复用。
"""

from __future__ import annotations

from pathlib import Path


def get_user_config_dir() -> Path:
    """用户级配置目录：~/.pyagent/"""
    return Path.home() / ".pyagent"


def get_project_config_dir(cwd: Path | None = None) -> Path:
    """项目级配置目录：<cwd>/.pyagent/"""
    base = cwd or Path.cwd()
    return base / ".pyagent"


def get_user_tools_dir() -> Path:
    """用户级工具目录：~/.pyagent/tools/"""
    return get_user_config_dir() / "tools"


def get_project_tools_dir(cwd: Path | None = None) -> Path:
    """项目级工具目录：<cwd>/.pyagent/tools/"""
    return get_project_config_dir(cwd) / "tools"


def get_user_skills_dir() -> Path:
    """用户级技能目录：~/.pyagent/skills/"""
    return get_user_config_dir() / "skills"


def get_project_skills_dir(cwd: Path | None = None) -> Path:
    """项目级技能目录：<cwd>/.pyagent/skills/"""
    return get_project_config_dir(cwd) / "skills"


def get_user_sessions_dir() -> Path:
    """用户级会话目录：~/.pyagent/sessions/"""
    return get_user_config_dir() / "sessions"


def get_user_logs_dir() -> Path:
    """用户级日志目录：~/.pyagent/logs/"""
    return get_user_config_dir() / "logs"


def get_user_output_dir() -> Path:
    """用户级输出目录：~/.pyagent/output/

    Agent 通过 write_file 等工具创建的文件默认保存到这里，
    避免文件散落在用户工作目录各处。
    """
    return get_user_config_dir() / "output"


def ensure_dir(path: Path) -> Path:
    """确保目录存在，不存在则创建（含父目录）。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_read_text(path: Path, encoding: str = "utf-8") -> str:
    """安全读取文本文件，统一用 UTF-8 编码。

    Python 默认 open() 在 Windows 上用 GBK，读 UTF-8 文件会报错，
    所以这里显式指定 encoding。
    """
    return path.read_text(encoding=encoding)


def safe_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """安全写入文本文件，统一用 UTF-8 无 BOM 编码。"""
    ensure_dir(path.parent)
    path.write_text(content, encoding=encoding)

"""全局配置模型。

用 pydantic-settings 的 BaseSettings 管理配置，
支持从 JSON 文件和环境变量加载。

配置优先级（从低到高）：
    默认值 → ~/.pyagent/settings.json → .pyagent/settings.json → 环境变量(PYAGENT_*) → CLI 参数
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseModel):
    """LLM 相关配置。"""

    model: str = "minimax/MiniMax-M3"
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float = 120.0


class AgentSettings(BaseModel):
    """Agent 运行时配置。"""

    system_prompt: str = "You are a helpful assistant."
    max_turns: int = 20
    tool_execution: str = "parallel"  # "parallel" | "sequential"

    #: 模型上下文窗口大小（token 数），用于压缩触发判断
    context_window: int = 128000
    #: 压缩触发阈值（0~1），达到 context_window * threshold 时触发压缩
    compaction_threshold: float = 0.8
    #: 压缩时保留尾部消息数（不被压缩的最近消息数）
    compaction_retained_tail: int = 10
    #: 是否启用上下文自动压缩
    enable_compaction: bool = True
    #: 是否启用分支功能
    enable_branching: bool = False


class SessionSettings(BaseModel):
    """会话存储配置。"""

    enabled: bool = True
    dir: Path | None = None  # None 时用默认 ~/.pyagent/sessions/


class LogSettings(BaseModel):
    """日志配置。"""

    level: str = "INFO"
    file_enabled: bool = True
    dir: Path | None = None  # None 时用默认 ~/.pyagent/logs/
    filename: str = "pyagent.log"  # 日志文件名


class Settings(BaseSettings):
    """PyAgent 全局配置。

    支持环境变量覆盖，前缀为 PYAGENT_，如 PYAGENT_LLM_MODEL=openai/gpt-4o。
    """

    model_config = SettingsConfigDict(
        env_prefix="PYAGENT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    log: LogSettings = Field(default_factory=LogSettings)

    # 是否启用内置工具
    enable_builtin_tools: bool = True

    # 是否启用内置技能
    enable_builtin_skills: bool = True

    @classmethod
    def load(cls, config_path: Path | None = None) -> Settings:
        """从 JSON 文件加载配置，再叠加环境变量。

        Args:
            config_path: 指定的配置文件路径。为 None 时使用默认搜索逻辑。
        """
        import json

        data: dict = {}
        if config_path and config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))

        return cls(**data)

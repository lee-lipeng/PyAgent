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


class HooksSettings(BaseModel):
    """内置 Hook 配置。

    控制 Runtime 在 setup 时自动注册哪些横切关注点。
    用户也可以通过禁用 ``enabled`` 完全跳过内置 hook，
    然后在外部自行调用 ``setup_*_hooks`` 来定制。
    """

    # 总开 False 时不注册任何内置 hook
    enabled: bool = True
    # 是否注册日志 hook（在 agent / tool 关键节点输出日志）
    enable_logging: bool = True
    # 是否注册权限 hook（在 BEFORE_TOOL 拦截被禁用的工具）
    enable_permission: bool = True
    # 被禁用的工具名集合（空集合表示不禁用任何工具）
    blocked_tools: set[str] = Field(default_factory=set)

    # 是否注册 Token 用量聚合 hook（AFTER_LLM 自动累加到 session）
    enable_usage_tracking: bool = True
    # 是否注册轮次计数 hook（BEFORE_LLM 自动自增 turn_count）
    enable_turn_counting: bool = True
    # 是否注册重复工具调用守卫 hook（连续相同调用超过阈值时拦截）
    enable_duplicate_guard: bool = True
    # 重复调用拦截阈值（连续触发次数）
    duplicate_guard_threshold: int = 3
    # 是否注册工具结果截断 hook（防止超长输出撑爆上下文）
    enable_result_truncation: bool = True
    # 工具结果最大字符数（超过则保留头尾各 1/4）
    result_truncation_max_chars: int = 8000
    # 是否注册会话自动落盘 hook（BEFORE_LLM 自动写盘）
    enable_auto_save: bool = True


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
    hooks: HooksSettings = Field(default_factory=HooksSettings)

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

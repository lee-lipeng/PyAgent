"""配置管理。

支持多级配置：默认值 → ~/.pyagent/settings.json → .pyagent/settings.json → 环境变量 → CLI 参数。
基于 pydantic-settings 实现，天然支持环境变量覆盖。
"""

from pyagent.config.settings import Settings

__all__ = ["Settings"]

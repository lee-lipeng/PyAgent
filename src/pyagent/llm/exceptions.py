"""LLM 层异常。"""

from __future__ import annotations


class LLMError(Exception):
    """LLM 调用相关错误的基类。"""


class ProviderError(LLMError):
    """Provider 返回错误（如限流、认证失败等）。"""


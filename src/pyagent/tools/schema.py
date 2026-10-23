"""工具 schema 生成辅助函数。

把 pydantic BaseModel 的 JSON Schema 转换为
OpenAI function calling 格式。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def model_to_openai_schema(model: type[BaseModel]) -> dict[str, Any]:
    """把 pydantic 模型转为 OpenAI function calling 的 parameters 格式。

    保留被 $ref 引用的 $defs / definitions，避免嵌套参数模型生成无效 Schema。
    只移除展示性的 title 字段。
    """
    schema = model.model_json_schema()

    # 不可移除 $defs / definitions：properties 中的 $ref 需要这些定义。
    schema.pop("title", None)

    # 清理 properties 中的 title
    properties = schema.get("properties", {})
    for prop in properties.values():
        if isinstance(prop, dict):
            prop.pop("title", None)

    return schema

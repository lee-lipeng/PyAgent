"""工具 schema 生成辅助函数。

把 pydantic BaseModel 的 JSON Schema 转换为
OpenAI function calling 格式。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def model_to_openai_schema(model: type[BaseModel]) -> dict[str, Any]:
    """把 pydantic 模型转为 OpenAI function calling 的 parameters 格式。

    pydantic 的 model_json_schema() 会包含 $defs、title 等额外字段，
    这里做清理，只保留 type / properties / required。
    """
    schema = model.model_json_schema()

    # 移除 pydantic 特有的字段
    for key in ("title", "$defs", "definitions"):
        schema.pop(key, None)

    # 清理 properties 中的 title
    properties = schema.get("properties", {})
    for prop in properties.values():
        if isinstance(prop, dict):
            prop.pop("title", None)

    return schema

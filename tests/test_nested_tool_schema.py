"""嵌套 Pydantic 模型的 JSON Schema 引用必须完整。"""

from pydantic import BaseModel

from pyagent.tools.schema import model_to_openai_schema


class FileRef(BaseModel):
    path: str


class Params(BaseModel):
    file: FileRef


def test_nested_schema_keeps_definitions():
    schema = model_to_openai_schema(Params)
    ref = schema["properties"]["file"]["$ref"]
    assert ref.startswith("#/$defs/")
    assert ref.rsplit("/", 1)[-1] in schema["$defs"]
    assert schema["$defs"]["FileRef"]["properties"]["path"]["type"] == "string"
    assert "title" not in schema

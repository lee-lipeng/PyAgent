"""检查 README 中的 Python 示例能被 Python 解析。"""

import ast
import re
from pathlib import Path


def test_readme_python_examples_are_valid_syntax():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(r"```python\n(.*?)\n```", readme, flags=re.DOTALL)
    assert len(blocks) >= 6
    for index, block in enumerate(blocks, start=1):
        compile(
            block,
            f"README.md Python example #{index}",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            dont_inherit=True,
        )


def test_readme_has_no_outdated_top_level_imports():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    assert "from pyagent import Runtime" not in readme
    assert "from pyagent import Settings" not in readme
    assert "runtime.setup()  # Web 服务启动" not in readme

---
name: testing
description: >-
  测试编写技能。当用户要求编写测试、补充测试用例、测试覆盖率时使用。
  指导 Agent 遵循 AAA 模式、覆盖边界条件、保持测试独立可重复。
  触发词：写测试、单元测试、test、pytest、测试用例、覆盖率。
---

# 测试编写技能

当用户要求编写或补充测试时，遵循结构化测试方法论。

## 测试原则

- **AAA 模式**：每个测试分为 Arrange（准备）、Act（执行）、Assert（断言）三段。
- **单一职责**：一个测试只验证一个行为，断言聚焦。
- **独立可重复**：测试不依赖执行顺序，不依赖外部状态，每次运行结果一致。
- **快速反馈**：单元测试应在秒级完成，慢测试标记为集成测试。
- **命名清晰**：`test_<行为>_<条件>_<预期>`，如 `test_parse_empty_input_returns_none`。

## 工作流程

1. **阅读被测代码**：用 `read_file` 阅读目标函数/类，理解输入输出和边界。
2. **识别测试场景**：
   - 正常路径（happy path）
   - 边界条件（空值、零值、最大值、最小值）
   - 异常路径（错误输入、资源不足、并发冲突）
   - 回归场景（之前出过 bug 的输入）
3. **编写测试**：按 AAA 模式编写，每个场景一个测试函数。
4. **运行验证**：用 `run_bash` 执行测试，确认全部通过。
5. **检查覆盖率**：如有覆盖率工具，检查未覆盖的分支。

## pytest 示例

```python
import pytest
from mymodule.parser import parse_config


class TestParseConfig:
    """parse_config 函数的测试。"""

    def test_valid_yaml_returns_dict(self):
        """正常路径：合法 YAML 返回字典。"""
        # Arrange
        content = "key: value\n"
        # Act
        result = parse_config(content)
        # Assert
        assert result == {"key": "value"}

    def test_empty_string_returns_empty_dict(self):
        """边界条件：空字符串返回空字典。"""
        result = parse_config("")
        assert result == {}

    def test_invalid_yaml_raises_error(self):
        """异常路径：非法 YAML 抛出异常。"""
        with pytest.raises(ValueError, match="YAML 解析失败"):
            parse_config("key: [unclosed")

    @pytest.mark.parametrize("input,expected", [
        ("a: 1", {"a": 1}),
        ("a: 1\nb: 2", {"a": 1, "b": 2}),
        ("nested:\n  key: val", {"nested": {"key": "val"}}),
    ])
    def test_various_inputs(self, input, expected):
        """参数化测试：多种输入格式。"""
        assert parse_config(input) == expected
```

## 测试分层

| 层级 | 范围 | 速度 | 工具 |
|------|------|------|------|
| 单元测试 | 单个函数/类 | 毫秒级 | pytest |
| 集成测试 | 模块间交互 | 秒级 | pytest + fixtures |
| 端到端测试 | 完整流程 | 分钟级 | 按需 |

## 常见陷阱

- **测试间耦合**：一个测试修改了全局状态，影响后续测试。
  → 每个测试用 fixture 隔离状态。
- **过度 mock**：mock 了太多内部逻辑，测试通过但实际代码有 bug。
  → 只 mock 外部依赖（网络、数据库、文件系统）。
- **断言不足**：只检查没抛异常，不检查返回值。
  → 明确断言预期输出。
- **测试不可重复**：依赖时间、随机数、网络。
  → 固定随机种子、mock 时间、使用临时目录。
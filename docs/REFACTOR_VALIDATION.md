# 重构验收与已知限制

本文件记录 [PR #1](https://github.com/lee-lipeng/PyAgent/pull/1) 的合并验收依据。优化以正确性、异常一致性、会话完整性与跨平台稳定性为目标，不涉及新的 Agent 产品功能。

## 核心修复

- **Agent / Hook：** 压缩不跨越不完整的工具调用批次；Hook 修改工具调用时保证 ID 与结果配对；明确权限拒绝、Abort、输出截断和异常完成状态。
- **Session / 工具：** 会话 ID 输入校验，原子保存 JSON；写入/编辑文件使用同目录临时文件原子替换；并行工具任务异常时回收未完成工作。
- **Runtime / CLI：** 生命周期保持在同一 asyncio 事件循环；拒绝同一 Runtime 上的并发运行；关闭浏览器后台任务。
- **配置 / 发现：** 默认值、用户配置、项目配置、环境变量按优先级递归合并；工具动态加载避免同名模块缓存污染，Skill 项目级优先。
- **浏览器：** WebSocket 迟到响应与取消回收；Tab 显式路由失败；截图辅助函数按需加载到 MAIN 执行世界；常见跨站网页 Origin 被拒绝。
- **跨平台：** POSIX 进程组与 Windows 进程树清理。Windows 平台使用 GitHub Actions Runner 验证父子进程中止。

## 验证命令

从仓库根目录执行：

```bash
uv sync --frozen --group dev
uv run --no-sync pytest -q
uv run --no-sync ruff check src/pyagent/core src/pyagent/hooks/builtin.py src/pyagent/hooks/manager.py tests
```

CI 工作流位于 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)。它在 Ubuntu 与 Windows 的 Python 3.12 环境运行完整 pytest，并对明确列出的核心模块执行 Ruff。请以 PR 最新提交对应的 CI 结果为最终准据，不要用旧提交的绿色结果替代。

此前版本（`5c8238a`）的 CI 验收记录：Ubuntu 81 passed / 1 skipped，Windows 78 passed / 4 skipped。后续文档提交需要重新通过 CI。

## 安全边界与未完成验证

- **不能保证绝对无 Bug。** 单测与静态检查的通过并不代表所有依赖环境及输入都被覆盖。
- 浏览器桥默认仅监听 `127.0.0.1`，实现了 WebSocket Origin 过滤，但允许没有 Origin 的本地原生客户端；**尚未实现连接凭证认证**，不能暴露到公共网络。
- Chrome 扩展截图的 MAIN world 辅助函数与按需注入逻辑已有针对性验证；**完整真实 Chrome/Edge 扩展端到端测试**（包含安装、连接、导航、截图和恢复）仍需手工/专用环境验证。
- 使用真实模型的流式错误、模型差异与 Token 使用预算仍可能存在 Provider 特定行为；当前 CI 主要为离线回归测试。
- 文件及浏览器工具会产生实际副作用；Abort 不能回滚已提交的外部操作。生产环境应限制工具权限和可访问目录。

以上限制作为合并后的公开已知风险保留，而不是声称这些场景已经通过验证。

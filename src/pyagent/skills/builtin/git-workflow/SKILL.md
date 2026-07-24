---
name: git-workflow
description: >-
  Git 工作流技能。当用户要求执行 Git 操作、管理分支、提交代码、
  处理合并冲突时使用。指导 Agent 遵循安全规范，避免破坏性操作。
  触发词：git、提交、commit、分支、merge、rebase、冲突、push。
---

# Git 工作流技能

当用户要求执行 Git 操作时，遵循安全规范和最佳实践。

## 常用操作

### 提交代码

```bash
# 查看变更
git status
git diff

# 暂存并提交（提交信息用祈使句，说明"做了什么"）
git add <files>
git commit -m "feat: 添加用户登录功能"
git commit -m "fix: 修复空指针异常"
git commit -m "refactor: 提取公共校验逻辑"
```

**提交信息规范**（Conventional Commits）：
- `feat:` 新功能
- `fix:` 修复 bug
- `refactor:` 重构（不改行为）
- `test:` 测试相关
- `docs:` 文档相关
- `chore:` 构建/工具/杂项

### 分支管理

```bash
# 创建并切换分支
git checkout -b feature/user-auth

# 查看分支
git branch -a

# 删除已合并的本地分支
git branch -d feature/user-auth
```

### 合并与变基

```bash
# 合并（保留完整历史）
git merge feature-branch

# 变基（线性历史，仅限未推送的提交）
git rebase main
```

### 解决冲突

```bash
# 合并冲突后
git status              # 查看冲突文件
# 手动编辑冲突文件，解决 <<<<<<< ======= >>>>>>> 标记
git add <resolved-files>
git commit              # 完成合并

# 变基冲突后
# 手动解决冲突
git add <resolved-files>
git rebase --continue   # 继续变基
# 想放弃变基
git rebase --abort
```

## 安全规范

- **不修改已推送的公共历史**：不要对 `main`/`develop` 等保护分支执行 `rebase` 或 `force push`。
- **force push 用 `--force-with-lease`**：比 `--force` 安全，避免覆盖他人提交。
- **不提交密钥**：不要把 API key、密码、token 写入代码或提交。
- **提交前检查**：`git diff --cached` 确认暂存内容，避免误提交调试代码。
- **小步提交**：一个提交只做一件事，不要把多个功能混在一个提交里。

## 历史考古

```bash
# 查找引入 bug 的提交
git bisect start
git bisect bad           # 当前版本是坏的
git bisect good <commit> # 这个版本是好的
# git 会自动二分，每次测试后标记
git bisect good          # 当前测试通过
git bisect bad           # 当前测试失败
git bisect reset         # 结束二分

# 查看某行代码的最后修改
git blame <file> -L <start>,<end>

# 查看某文件的历史变更
git log -p <file>
```

## 注意事项

- 执行 Git 命令前先用 `git status` 确认当前状态。
- 不确定操作是否安全时，先问用户。
- 破坏性操作（`reset --hard`、`clean -fd`、`force push`）需用户确认。
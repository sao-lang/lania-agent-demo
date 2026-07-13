---
applyTo: '.git/**'
---

# Git Rules

- **全量推送**（用户说"全量/全部/所有"）：`git add . && git commit -m "<msg>" && git push`
- **精细推送**（常规提交）：仅暂存本次修改的文件：`git add <files> && git commit -m "<msg>" && git push`
- **Message 规范**：`<type>(<scope>): <subject>` 格式
  - 例：`feat(theme): 增加主题类型`、`fix(auth): 修复 Token 过期未拦截`
- 提交完成后列出本次修改总结

---
name: ai-coding-rules
description: 这是一个通用的 AI 编码规则 skill。它不重复解释规则文件内容，而是用于指导大模型在需要时按任务类型加载对应的规则说明文件。
---

# AI 编码规则

## 作用

当需要在代码仓库中完成开发任务时，优先使用这份 skill。它的职责是：根据当前任务类型，决定加载哪一个规则文件，并把规则文件中的约束作为执行依据。

## 何时使用

适用于以下场景：

- 处理代码实现、修 bug、重构、测试、文档或调试
- 需要在不同语言、框架或 IDE 环境下保持一致的开发行为
- 代码提交、代码回退
- 其他有代码变动的情况

## 何时加载什么规则文件

在执行任务前，先判断当前任务属于哪一类，然后按需加载对应文件：

- 通用开发约束，必须加载：加载 rules/00-base.instructions.md
- TypeScript / TSX 相关代码：加载 rules/01-typescript.instructions.md
- 代码重构：加载 rules/02-refactor.instructions.md
- 测试相关任务：加载 rules/03-testing.instructions.md
- Git / 变更管理相关：加载 rules/04-git.instructions.md
- Graphify / 架构关系分析相关：加载 rules/05-graphify.instructions.md
- 文档更新：加载 rules/06-doc.instructions.md
- 发布 / changelog：加载 rules/07-release.instructions.md
- Dart 相关：加载 rules/08-dart.instructions.md
- Rust 相关：加载 rules/09-rust.instructions.md
- Python 相关：加载 rules/10-python.instructions.md
- Go 相关：加载 rules/11-go.instructions.md
- 调试问题：加载 rules/12-debug.instructions.md

## 使用原则

- 只加载与当前任务相关的规则文件，不要全部加载。
- 如果多个规则同时相关，优先遵循更具体、更贴近当前任务的规则。
- 规则文件内容由其自身定义，skill 只负责选择和触发。

## 处理方式

### 第一步：理解任务
- 理解用户需求，明确任务类型（开发/重构/修复/测试/文档等）

### 第二步：加载规则文件
- 根据任务类型加载对应的规则文件（参考上方"何时加载什么规则文件"）
- 始终加载 `rules/00-base.instructions.md`

### 第三步：输出修改方案（R8）
- **改源文件前，必须先输出修改方案征得用户同意**
- 方案应包含：修改目标、涉及文件、变更要点、潜在影响
- 等待用户确认后再进入实现

### 第四步：按规则实现
- 按已加载的规则文件要求实现代码

### 第五步：代码验证（R10）
- 修改完成后必须运行代码检查，确保无错误
- 根据项目语言加载对应的规则文件，按其中的验证命令执行（如 TypeScript 加载 `01-typescript.instructions.md`、Python 加载 `10-python.instructions.md` 等）
- 如检查出错，立即修复并重新验证

### 第六步：记录与提交
- 按 `rules/00-base.instructions.md` 的 R9 要求追加记录到 `overview.md`
- 按 `rules/04-git.instructions.md` 执行 Git 提交

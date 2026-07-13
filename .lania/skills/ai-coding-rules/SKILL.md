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

## 何时加载什么规则文件

在执行任务前，先判断当前任务属于哪一类，然后按需加载对应文件：

- 通用开发约束：加载 rules/00-base.instructions.md
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

- 先理解任务
- 再判断需要加载的规则文件
- 再按规则文件要求执行对应步骤
- 最后完成实现、验证和说明

# Architecture Docs Index

## 0. 当前实施状态（2026-07-09）

Harness 核心重构已完成：

- **HarnessKernel / Recipe / Stage 已删除**：这些抽象已从代码库中移除。LangGraph 是唯一的工作流引擎。
- **core/ 目录已删除**：`app/harness/core/`（kernel.py, recipe.py, stage.py, runtime_context.py, prompt_registry.py, sandbox_extensions.py）已删除。
- **recipes/ 目录已删除**：`app/harness/recipes/`（query_recipe.py, task_recipe.py）已删除。
- **extensions/ 目录已删除**：`app/harness/extensions/` 已删除。
- **RAG 双重身份已实现**：RAG 既是独立应用（/query、/chat 端点 → QueryWorkflowOrchestrator → LangGraph → RagFacade），也是注册 Tool（/agent、/tasks 端点 → ExecutionHarness.run_tool("rag_*")）。
- **ExecutionHarness 零外部依赖**：Harness 核心不再 import 任何具体 Capability，只依赖 ToolRegistry 抽象。
- **capabilities dict 注入**：ToolContext 通过 `deps` dict + `__getattr__` 代理实现动态 Capability 注入。

## 1. 目录内容

本目录下保留的文档：

| 文件 | 角色 | 说明 |
|---|---|---|
| `agent-capability-management-design.md` | 核心管理 | session/memory/skill/tool/command/permission/sub-agent/hook/context/tech-stack 的归口设计 |
| `harness-capability-integration.md` | 结合方案 | 各类 agent 能力如何接入 harness engineering |

## 2. 项目文档总览

| 文档 | 位置 | 说明 |
|------|------|------|
| **架构总览** | [`架构.md`](../../架构.md) | 完整分层架构、模块职责、数据主链、通信链路 |
| **README** | [`README.md`](../../README.md) | 项目入口文档 |
| **Agent Platform 设计** | [`agent-platform-architecture.md`](../agent-platform-architecture.md) | Mode + Capability 模型设计 |
| **实施路线图** | [`agent-platform-roadmap.md`](../agent-platform-roadmap.md) | 分阶段实施计划 |
| **记忆系统改造** | [`memory-system-redesign.md`](../memory-system-redesign.md) | 五层记忆系统设计 |
| **能力管理** | [`agent-capability-management-design.md`](agent-capability-management-design.md) | 能力归口与管理 |
| **能力集成** | [`harness-capability-integration.md`](harness-capability-integration.md) | 能力接入 harness |
| **运维手册** | [`operations/remote-provider-runbook.md`](operations/remote-provider-runbook.md) | 远程 Provider 运维 |

## 3. 已删除的历史文档

以下文档已随 Harness 核心重构完成而删除（其内容已过时，引用的 HarnessKernel/Recipe/Stage/core/recipes/ 等组件已从代码库移除）：

- ~~harness-composition-refactor-plan.md~~ — 重构方案（已完成）
- ~~harness-composition-migration-checklist.md~~ — 迁移清单（已完成）
- ~~harness-runtime-contracts.md~~ — 将已删除组件列为稳定接口
- ~~harnessed-react-agent-redesign.md~~ — 历史源文档
- ~~harness-final-cohesion-shape.md~~ — 讨论删除已删除的概念
- ~~workflow-langgraph-refactor-plan.md~~ — LangGraph 切换（已完成，自述"重构已经完成主体落地"）
- ~~harness-unification-refactor.md~~ — 双线合一（已完成）

## 4. 使用约束

1. 每份文档只回答一类问题。
2. 新文档必须在本 `README` 中登记角色。
3. 若新文档与已有文档高度重叠，应优先补充已有文档而不是新增平行版本。
4. 架构文档的核心参考是 `架构.md`，其余文档是补充说明。
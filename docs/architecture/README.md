# Architecture Docs Index

## 0. 当前实施状态（2026-06-23）

当前可按以下状态理解本目录中的迁移文档：

- **Phase 1 已完成**：`harness/core` 契约占位、`RagFacade`、`rag_*` tools、container 注入链都已经落地。
- **Phase 2 已完成**：`query` 与 `task/document_analysis` 主链已经统一切到 `rag_*` tool surface / `RagFacade`，不再在 workflow / orchestrator 内平行直连 `KnowledgeCapability`。
- **Phase 3 已推进到中段**：`ExecutionHarness`、`ContextHarness` 已经收缩为兼容 facade，`reflection/recovery` 已迁到 `harness/extensions/query/`，`PolicyEngine` / `GuardrailEngine` 也已拆出内部组件。
- **Phase 4 已实质启动**：`query_nodes` 已不再直接依赖 `RagQueryEngine` 私有 helper，也不再直接绑定 `RagQueryEngine` 本体；`query_orchestrator` 已统一走 graph-driven workflow，不再保留 classic/fast-path 作为 query 主执行分支。
- **Workflow LangGraph 切换已完成**：`query / chat` 与 `task/document_analysis` 的 workflow 主执行路径已经切回 LangGraph compiled graph；原 route-loop 兼容执行器已下线，不再保留在仓库中。
- **当前下一步是 Phase 4 收尾**：旧 knowledge tool 名已经从公开 `task tool` surface 下线，后续可继续考虑是否删除对应兼容实现代码与测试。

本次状态更新对应的实际代码范围主要包括：

- `app/rag/facade.py`
- `app/agents/tools/rag_tools.py`
- `app/workflows/query_orchestrator.py`
- `app/workflows/query_nodes.py`
- `app/workflows/tasks/task_orchestrator.py`
- `app/workflows/tasks/document_analysis_nodes.py`
- `app/workflows/tasks/document_analysis_task_adapter.py`
- `app/agents/subagents.py`
- `app/agents/planner.py`
- `app/harness/execution.py`
- `app/harness/context.py`
- `app/harness/reflection.py`
- `app/harness/recovery.py`
- `app/harness/policy.py`
- `app/harness/guardrails.py`
- `app/harness/extensions/query/`
- `app/harness/components/`
- `app/workflows/query_runtime.py`

## 1. 目的

本目录下的文档已经覆盖了：

- 顶层重构方向
- 运行时契约
- 能力管理
- harness 与能力结合方式
- workflow 层专项改造方案
- 迁移清单
- 最终内聚目标

但这些文档存在明显层次差异：有的是顶层方向文档，有的是实施文档，有的是补充判断文档。  
为了方便 AI 在上下文有限时快速加载，这里给出一份**最小加载顺序**和**文档角色表**。

## 2. 推荐加载顺序

### 2.1 最小上下文集

如果上下文预算紧，只加载以下 4 份：

1. [harness-composition-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-refactor-plan.md)
2. [harness-runtime-contracts.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-runtime-contracts.md)
3. [agent-capability-management-design.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/agent-capability-management-design.md)
4. [harness-composition-migration-checklist.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-migration-checklist.md)

这 4 份已经足够回答：

- 目标架构是什么
- 稳定接口是什么
- 各类能力归谁管理
- 实际实施顺序是什么

### 2.2 扩展上下文集

如果还需要补充判断和结合方式，再加读：

5. [harness-capability-integration.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-capability-integration.md)
6. [harness-final-cohesion-shape.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-final-cohesion-shape.md)
7. [workflow-langgraph-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/workflow-langgraph-refactor-plan.md)

这三份主要回答：

- 各类能力怎么接入 harness engineering
- 架构还能如何继续内聚
- 当明确要把 workflow 主执行路径切回 LangGraph 时，应如何限定边界与实施顺序

### 2.3 历史源文档

只有在需要追溯最初决策背景时，再读：

8. [harnessed-react-agent-redesign.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harnessed-react-agent-redesign.md)

这份是最早的总设计文档，信息量最大，但也最重，不适合作为默认上下文入口。

## 3. 文档角色表

| 文件 | 角色 | 是否建议默认加载 | 说明 |
|---|---|---|---|
| `harness-composition-refactor-plan.md` | 核心设计 | 是 | 当前重构方案的主文档 |
| `harness-runtime-contracts.md` | 核心契约 | 是 | 稳定接口与兼容层边界 |
| `agent-capability-management-design.md` | 核心管理 | 是 | `session/memory/skill/tool/...` 的归口设计 |
| `harness-composition-migration-checklist.md` | 核心实施 | 是 | 文件级迁移清单 |
| `harness-capability-integration.md` | 结合方案 | 视需要 | 能力如何接入 harness engineering |
| `harness-final-cohesion-shape.md` | 终态判断 | 视需要 | 哪些概念应继续收掉 |
| `workflow-langgraph-refactor-plan.md` | 专项方案 | 视需要 | 只改 workflow 层时的 LangGraph 重构边界与阶段 |
| `harnessed-react-agent-redesign.md` | 历史源文档 | 否 | 原始顶层背景和演进判断 |

## 4. 建议给 AI 的加载策略

### 场景 A：要理解当前重构方向

读取：

- `README.md`
- `harness-composition-refactor-plan.md`
- `harness-runtime-contracts.md`

### 场景 B：要开始写代码

读取：

- `README.md`
- `harness-composition-refactor-plan.md`
- `harness-runtime-contracts.md`
- `harness-composition-migration-checklist.md`

### 场景 C：要理解 agent 能力怎么归口

读取：

- `README.md`
- `agent-capability-management-design.md`
- `harness-capability-integration.md`

### 场景 D：要继续讨论抽象是否还能内聚

读取：

- `README.md`
- `harness-final-cohesion-shape.md`
- `harness-runtime-contracts.md`

### 场景 E：要把 workflow 主执行路径切回 LangGraph

读取：

- `README.md`
- `workflow-langgraph-refactor-plan.md`
- `harness-runtime-contracts.md`

## 5. 使用约束

为避免文档继续发散，后续新增文档时建议遵守：

1. 每份文档只回答一类问题。
2. 新文档必须在本 `README` 中登记角色。
3. 若新文档与已有文档高度重叠，应优先补充已有文档而不是新增平行版本。
4. 实施类变更优先更新 `migration-checklist`，不要把实施步骤散写到原则文档中。

## 6. 一句话结论

这个目录后续建议按下面的层次使用：

- `refactor-plan`：讲目标架构
- `runtime-contracts`：讲稳定接口
- `agent-capability-management`：讲能力归口
- `workflow-langgraph-refactor-plan`：讲 workflow 层专项改造
- `migration-checklist`：讲实施顺序
- `capability-integration / final-cohesion-shape`：讲补充判断
- `harnessed-react-agent-redesign`：讲历史背景

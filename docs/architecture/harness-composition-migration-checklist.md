# Harness 组件化重构实施清单

> 文档角色：核心实施文档
>  
> 是否建议默认加载：是（实现阶段）
>  
> 适合回答的问题：
> - 先动哪些文件
> - 哪些类保留兼容
> - 每个阶段怎么验收
>  
> 建议搭配阅读：
> - `harness-composition-refactor-plan.md`
> - `harness-runtime-contracts.md`

## 1. 文档目的

本文是以下两份设计文档的实施清单：

- [harness-composition-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-refactor-plan.md)
- [harness-runtime-contracts.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-runtime-contracts.md)

目标不是再次解释设计，而是把重构拆成可执行的文件级任务，并明确：

- 第一批新增什么
- 第一批替换什么
- 什么需要兼容
- 什么后续可以删除

## 2. 总体迁移策略

本次重构采用四阶段迁移：

1. **先立接口**
2. **再收 RAG 调用面**
3. **再拆 Harness 厚层**
4. **最后删兼容路径**

约束：

- 每一阶段都必须保持现有 `query / chat / task` 可运行
- 不做一次性推平式重构
- 任何新路径必须先通过兼容层接入，再逐步替换旧调用点

## 3. Phase 1：立接口，不改行为

### 3.1 新增文件

第一批建议新增以下文件：

```text
app/harness/core/kernel.py
app/harness/core/recipe.py
app/harness/core/stage.py
app/harness/core/runtime_context.py
app/rag/facade.py
app/tools/rag_tools.py
```

### 3.2 文件职责

#### `app/harness/core/kernel.py`

新增 `HarnessKernel`，但初期只做薄封装：

- 接收 recipe、state、ctx
- 顺序执行 stage
- 暂不重写全部 workflow 逻辑

#### `app/harness/core/recipe.py`

定义：

- `HarnessRecipe`
- `BaseRecipe`

初期先让 query/task 只做声明式 recipe，占住接口位置。

#### `app/harness/core/stage.py`

定义：

- `HarnessStage`
- `BaseStage`

初期只要求 stage 能表达“单步输入输出”，不要求完全替换现有 nodes。

#### `app/harness/core/runtime_context.py`

定义统一 `RuntimeContext`。

初期只放稳定运行时依赖：

- trace
- tool registry
- executor
- subagent runtime
- policy/guardrail

#### `app/rag/facade.py`

新增 `RagFacade`，先做统一门面：

- `load_document_context`
- `retrieve_evidence`
- `grounded_answer`
- `grounded_query`

初期内部允许转调现有：

- `KnowledgeCapability`
- `RagRetrievalService`
- `RagQueryEngine`

#### `app/tools/rag_tools.py`

新增 RAG tool：

- `rag_load_document_context`
- `rag_retrieve_evidence`
- `rag_grounded_answer`
- `rag_grounded_query`

这些 tool 初期直接依赖 `RagFacade`。

### 3.3 Phase 1 修改点

#### `app/agents/tools/registry.py`

修改点：

- 注册新增 `rag_*` tool
- 保持旧知识类 tool 兼容存在

#### `app/container.py`

修改点：

- 新增 `self.rag_facade`
- 注入 `rag_tools`
- 暂不移除旧 knowledge capability 装配

### 3.4 Phase 1 验收

- `rag_tools.py` 已创建并完成注册
- `RagFacade` 已可通过 container 注入
- 不改现有调用路径时，系统仍可正常运行

## 4. Phase 2：统一 RAG 调用入口

> 状态更新（2026-06-23）：
>
> - 本阶段已完成。
> - `query` 主链已经改为统一通过 `rag_*` tool surface / `RagFacade` 访问知识能力。
> - `task/document_analysis` 主链也已经切到同一套入口，并移除了 `TaskWorkflowOrchestrator` 的平行 capability 构造。
> - 本阶段完成后，下一步应转入 Phase 3，而不是继续扩张新的 RAG 接入分支。

### 4.1 重点修改文件

按优先级修改以下文件：

1. `app/workflows/query_nodes.py`
2. `app/workflows/tasks/document_analysis_nodes.py`
3. `app/workflows/query_orchestrator.py`
4. `app/rag/query_engine.py`

### 4.2 修改目标

#### `app/workflows/query_nodes.py`

目标：

- 把直接调用 `knowledge_capability` 的位置改为调用 `rag_*` tool
- 如果临时不方便完全改掉，至少先转成调用 `RagFacade`

验收：

- query nodes 内不再直接 `build_knowledge_capability()`
- 已完成：当前 query 检索 fallback 已通过 `RagFacade` 收口，知识调用面统一进入 `rag_*` 工具链

#### `app/workflows/tasks/document_analysis_nodes.py`

目标：

- 文档分析节点中的知识相关步骤统一调 `rag_*` tool
- 保留旧路径兜底，但标注为兼容路径

验收：

- 节点里的知识操作统一通过 executor/tool 进入
- 已完成：`collect_document_context` / evidence 收集链已切到 `rag_load_document_context`、`rag_retrieve_evidence`、`rag_retrieve_graph_evidence`

#### `app/workflows/query_orchestrator.py`

目标：

- 不再在 orchestrator 中兜底构造新的 knowledge capability 实例
- 通过已注入的 facade 或 tool surface 访问知识能力

验收：

- orchestrator 不再承担 capability factory 的职责
- 已完成：query orchestrator 仅复用已注入的 `rag_facade` / `execution_harness`

#### `app/rag/query_engine.py`

目标：

- 逐步把对 `knowledge_capability` 的分支判断下沉到 `RagFacade`
- `query_engine` 自己不再同时扮演 facade 和编排器

验收：

- `query_engine` 的职责开始收缩为 RAG 内部实现组件
- 已完成部分：`query_engine` 已支持外部注入 `knowledge_capability`，query 链不再依赖它自行平行构造 capability

### 4.3 兼容策略

迁移期间允许：

- `rag_*` tool 内部转调旧知识能力
- 旧逻辑保留 fallback 分支

但必须做到：

- 新增代码不再直接使用旧路径

### 4.4 Phase 2 验收

- `query_nodes.py` 不再直接触达 knowledge capability
- `document_analysis_nodes.py` 的知识调用统一进入 tool executor
- `query_orchestrator.py` 不再创建平行 capability 入口
- `task_orchestrator.py` 不再自行 `build_knowledge_capability()`
- `document_analysis_task_adapter.py`、`ContextHarness`、`EvidenceAgent`、`TaskPlanner` 已统一切到 `rag_*` 工具名

## 5. Phase 3：拆 Harness 厚层

> 状态更新（2026-06-23）：
>
> - 本阶段已完成主体拆分，当前进入收尾阶段。
> - `ExecutionHarness` 已拆成 facade + `ToolExecutor / ExecutionPolicyResolver / ExecutionHooks / FallbackHandler`。
> - `ContextHarness` 已拆成 facade + `TaskContextBuilder / QueryContextBuilder`。
> - `reflection/recovery` 已迁到 `app/harness/extensions/query/`，旧根目录模块仅保留兼容转发。
> - `PolicyEngine` 与 `GuardrailEngine` 也已收缩为 facade，内部职责分别下沉到 profile/check/evaluator 与 decision/raiser 组件。

### 5.1 重点修改文件

1. `app/harness/execution.py`
2. `app/harness/context.py`
3. `app/harness/reflection.py`
4. `app/harness/recovery.py`
5. `app/harness/guardrails.py`
6. `app/harness/policy.py`

### 5.2 拆分建议

#### `app/harness/execution.py`

目标拆分为：

- `ToolExecutor`
- `ExecutionPolicy`
- `FallbackHandler`
- `ExecutionHooks`

具体动作：

- 保留 `ExecutionHarness` 作为 facade
- 把超时、重试、sandbox、trace/fallback 分离出去

#### `app/harness/context.py`

目标拆分为：

- `BaseContextProvider`
- `QueryContextProvider`
- `TaskContextProvider`

具体动作：

- 把 query/task 专用上下文构造从通用 harness 中挪出

#### `app/harness/reflection.py` 和 `app/harness/recovery.py`

目标：

- 迁移到 `app/harness/extensions/query/`
- 或者改造成通用接口 + query 实现类

#### `app/harness/guardrails.py` 和 `app/harness/policy.py`

目标：

- 收口为平台接口
- document-analysis / report 语义迁到具体 profile

### 5.3 Phase 3 验收

- 已完成：`ExecutionHarness` 只剩 facade/兼容壳职责，执行控制下沉到 `app/harness/components/tool_executor.py`、`execution_policy.py`、`execution_hooks.py`、`fallback_handler.py`
- 已完成：`ContextHarness` 不再混放 query/task 专用实现，构造逻辑下沉到 `app/harness/components/context_builders.py`
- 已完成：`reflection/recovery` 已迁到 `app/harness/extensions/query/`
- 已完成：`policy/guardrail` 的内部职责已拆分，`app/harness/` 根目录中的领域语义已明显收缩

## 6. Phase 4：清理兼容路径

> 状态更新（2026-06-23）：
>
> - `query_nodes` 已不再直接依赖 `KnowledgeCapability` 或 `RagQueryEngine` 私有 helper。
> - `QueryWorkflowRuntime` / `QueryEngineWorkflowRuntimeAdapter` 已落地，`query_orchestrator` 与 `query_graph` 已改为通过稳定 runtime surface 装配 query workflow。
> - `query_orchestrator` 中原先直接拼 classic 私有 helper 的 fast path 已退出 query 主执行路径；当前 query/chat 默认统一走 LangGraph compiled graph。
> - 内部 query runtime 默认 tool registry 已收敛到 `rag_*` 工具；相关注册逻辑已集中到 `app/agents/tools/defaults.py`。
> - 旧 knowledge tool 名已从公开 `task tool` surface 下线，`container.py` 现在只暴露 `rag_*` 主路径工具名。

### 6.1 重点修改文件

1. `app/container.py`
2. `app/capabilities/knowledge/service.py`
3. `app/rag/query_engine.py`
4. `app/workflows/query_orchestrator.py`
5. `app/workflows/query_nodes.py`
6. `app/workflows/tasks/document_analysis_nodes.py`

### 6.2 清理目标

#### `app/container.py`

清理目标：

- 减少重复 knowledge capability 装配
- 保证上层只依赖 `RagFacade` 或 tool surface

#### `app/capabilities/knowledge/service.py`

清理目标：

- 明确它是 domain capability 实现，而不是 workflow 入口
- 避免继续承载 query 编排职责

#### `app/rag/query_engine.py`

清理目标：

- 删除或收缩平行执行路径
- 让其回归 RAG 域内部组件身份

### 6.3 Phase 4 验收

- 已完成：`workflow` 层不再直接依赖 `KnowledgeCapability`
- 已完成：`container.py` 已去掉重复本地 capability 实例构造，公开 `task tool` surface 也已只保留 `rag_*` 主路径工具名
- 进行中：兼容层已显著收缩，`query runtime` 主链改走稳定 surface；剩余工作主要是决定是否删除旧 knowledge tool 实现代码

## 7. 文件处理建议表

### 7.1 新增

建议新增：

- `app/harness/core/kernel.py`
- `app/harness/core/recipe.py`
- `app/harness/core/stage.py`
- `app/harness/core/runtime_context.py`
- `app/rag/facade.py`
- `app/tools/rag_tools.py`

### 7.2 迁移

建议迁移或拆分：

- `app/harness/execution.py`
- `app/harness/context.py`
- `app/harness/reflection.py`
- `app/harness/recovery.py`

### 7.3 瘦身

建议瘦身：

- `app/rag/query_engine.py`
- `app/workflows/query_orchestrator.py`
- `app/capabilities/knowledge/service.py`

### 7.4 保留兼容

短期保留兼容：

- `ExecutionHarness`
- `ContextHarness`
- 旧 knowledge tools
- 旧 workflow 节点中的 fallback 分支

### 7.5 后续废弃候选

待迁移完成后评估废弃：

- workflow/node 内直接调 capability 的代码路径
- `query_engine` 中与 facade 重复的分支
- container 中重复实例化的 knowledge capability 引用

## 8. 任务拆分建议

建议把实施任务拆成以下工作项：

### Task A：建立新接口与骨架

- 新增 `HarnessKernel / Recipe / Stage / RuntimeContext`
- 新增 `RagFacade`
- 新增 `rag_tools`

### Task B：打通容器装配

- container 注入 `RagFacade`
- 注册 `rag_*` tools
- 保持现有能力兼容

### Task C：替换 query 调用面

- 改 `query_nodes.py`
- 改 `query_orchestrator.py`

### Task D：替换 task 调用面

- 改 `document_analysis_nodes.py`

### Task E：拆 Harness

- 拆 `ExecutionHarness`
- 拆 `ContextHarness`

### Task F：删重复路径

- 清理旧 knowledge capability 平行入口
- 收缩 `query_engine`

## 9. 风险点

### 9.1 最大风险

最大风险不是代码量，而是“新旧调用面同时存在时再次长出第三套入口”。

因此必须坚持：

- 新代码只走 `RagFacade` 或 `rag_*` tool
- 不再新增对 `KnowledgeCapability` 的直接依赖点

### 9.2 次级风险

- `ExecutionHarness` 拆分后 trace / fallback 行为变化
- `query` 与 `task` 路径在兼容期表现不一致
- container 注入关系变动导致循环依赖

### 9.3 风险控制

建议每阶段都做：

1. 保持旧路径兜底
2. 增加最小回归测试
3. 优先改 query，再改 task
4. 兼容层只准变薄，不准继续塞新逻辑

## 10. 检查清单

### Phase 1 Checklist

- [x] 已新增 `HarnessKernel`
- [x] 已新增 `HarnessRecipe`
- [x] 已新增 `HarnessStage`
- [x] 已新增 `RuntimeContext`
- [x] 已新增 `RagFacade`
- [x] 已新增 `rag_*` tools
- [x] container 已注册 `rag_*` tools

### Phase 2 Checklist

- [x] `query_nodes.py` 不再直接调用 knowledge capability
- [x] `document_analysis_nodes.py` 知识调用统一经 tool executor
- [x] `query_orchestrator.py` 不再自行 build capability
- [x] `task_orchestrator.py` 不再自行 build capability
- [x] `document_analysis` 相关 planner / adapter / subagent / context 默认路由已统一切到 `rag_*`
- [x] `query_engine.py` 已支持外部注入 capability，query 链不再依赖其平行构造入口

### Phase 3 Checklist

- [x] `ExecutionHarness` 已拆出 executor/policy/fallback/hooks
- [x] `ContextHarness` 已拆出 context providers
- [x] `reflection/recovery` 已迁出通用 harness 根目录
- [x] `guardrail/policy` 平台接口和领域 profile 已分离

### Phase 4 Checklist

- [ ] 旧平行 knowledge 入口已清理
- [ ] workflow 不再直接依赖 capability 内部类
- [ ] container 不再重复装配等价知识能力入口
- [ ] 兼容层只剩薄转发壳

## 11. 建议开工顺序

如果只开第一轮，我建议严格按这个顺序：

1. `app/rag/facade.py`
2. `app/tools/rag_tools.py`
3. `app/container.py`
4. `app/workflows/query_nodes.py`
5. `app/workflows/query_orchestrator.py`
6. `app/workflows/tasks/document_analysis_nodes.py`
7. `app/harness/execution.py`
8. `app/harness/context.py`

原因：

- 先立知识调用边界
- 再处理 harness 厚层
- 这样不会在拆 harness 时继续背着旧 RAG 直连路径

## 12. 一句话结论

这次重构最重要的不是“拆多少文件”，而是先把唯一执行入口和唯一知识调用面立住。

优先级应始终是：

`RAG Facade + rag_tools -> workflow 调用面收口 -> Harness 拆薄 -> 删除兼容路径`

# Workflow LangGraph 重构方案

> 文档角色：专项设计 / 实施前方案文档
>
> 是否建议默认加载：当目标明确是“把 workflow 层切回 LangGraph 执行”时建议加载
>
> 适合回答的问题：
> - 这次为什么只改 workflow，不改 harness
> - query / task 两条主链如何迁到 LangGraph
> - 迁移顺序、风险和回滚边界是什么
>
> 建议搭配阅读：
> - `harness-runtime-contracts.md`
> - `harness-composition-migration-checklist.md`

> 状态更新（2026-06-23）：
>
> - 本文描述的重构已经完成主体落地。
> - `query / chat` 主链已切到 `build_query_graph(...).invoke(...)`。
> - `task/document_analysis` 主链已切到 `build_document_analysis_graph(...).invoke(...)`。
> - 曾作为兼容层保留的 route-loop 执行器已完成下线，不再保留在代码库中。

## 1. 文档目的

本文只回答一个问题：

- 在**不改 harness runtime 治理层**的前提下，如何把 `workflow` 主执行路径切回 `LangGraph`

本文不讨论：

- 是否继续扩大 `harness` 抽象层
- 是否把 `ExecutionHarness / ContextHarness / Policy / Guardrail` graph 化
- 是否重新定义 `TaskSpec / TaskRun / ResultContract`

换句话说，本方案是一次**workflow 编排层替换**，不是一次新的平台层重构。

## 2. 当前现状

在本轮改造完成前，仓库里曾同时存在两套表达：

1. **LangGraph 图定义仍然存在**
   - `app/workflows/query_graph.py`
   - `app/workflows/tasks/document_analysis_graph.py`

2. **真实主执行路径一度回到自研 route loop**
   - `app/workflows/query_orchestrator.py`
   - `app/workflows/tasks/task_orchestrator.py`
   - 当时还保留了一层共享 route-loop 执行器作为兼容路径（现已下线）

当时的问题不在于 node 语义缺失，而在于：

- `query` 的 `langgraph` 配置名与真实执行实现不一致
- `task` 侧图定义和运行时主链分离，存在双轨维护成本
- 后续如果还要继续讨论 workflow 分支、可视化与 replay 路径，当前表达会越来越拧巴

因此，本次改造的目标不是“引入 LangGraph”，而是把已经存在但未成为主链的 graph runtime 真正接回去。当前代码已经按这个目标落地完成。

## 3. 核心判断

### 3.1 为什么只改 workflow

因为当前最适合 `LangGraph` 的层，是：

- 节点声明
- 条件分支
- 终止边
- 编排可视化
- graph 级 checkpoint / resume 入口

而不是：

- tool execution policy
- sandbox
- fallback
- trace/memory bookkeeping
- artifact/result contract 收口

这些仍然应由 `harness` 和其 components 负责。

### 3.2 这次不动什么

以下对象明确不作为本次改造目标：

- `app/harness/`
- `app/agents/tools/`
- `app/rag/facade.py`
- `app/workflows/query_nodes.py` 的业务语义
- `app/workflows/tasks/document_analysis_nodes.py` 的业务语义
- `TaskSpec / TaskRun / CheckpointRecord / ResultContract` 模型

### 3.3 这次真正要改什么

只改以下两类内容：

1. **workflow 主执行入口**
   - 让 orchestrator 真正调用 graph compiled app

2. **graph 与现有 checkpoint / replay / error handling 的接缝**
   - 让现有 runtime state、异常包装、持久化与 graph 执行重新对齐

## 4. 目标边界

### 4.1 Query / Chat workflow

目标：

- `QUERY_ORCHESTRATOR=langgraph` 时，真实执行改为 `build_query_graph(...).invoke(...)`
- 保持 `query / chat / stream_query / stream_chat / replay_from_checkpoint` 对外行为不变
- 保持 `QueryWorkflowNodes` 为唯一节点语义来源

不做：

- 不把 `classic` 模式删掉
- 不重写 `QueryWorkflowNodes`
- 不新增第二套 query state schema

### 4.2 Task workflow

目标：

- `TaskWorkflowOrchestrator` 的主执行改为 `build_document_analysis_graph(...).invoke(...)`
- 保持 `DocumentAnalysisNodes` 为唯一节点语义来源
- 保持现有 `TaskDetail / TaskRunDetail / replay_task_run / resume_task_run` 能力可用

不做：

- 不把 skill 体系改成 graph registry
- 不把 `document_summary` 等 skill 重新设计成独立 graph 文件
- 不把 task service / worker 重写为 graph-aware service

## 5. 设计原则

### 5.1 节点语义不变，执行器替换

优先保持：

- node handler
- route function
- state shape
- trace / event / checkpoint 行为

变化只应发生在：

- orchestrator 内部的执行驱动方式

### 5.2 保持 graph 是 workflow 层对象

LangGraph 的责任只到：

- graph compile
- graph invoke
- graph route transition

不要把以下逻辑挪进 graph infra：

- tool runtime control
- policy / sandbox / fallback
- memory / artifact persistence

### 5.3 兼容 replay 与失败落账

迁移不能牺牲现有：

- checkpoint replay
- recoverable run
- step_failed 事件
- workflow_completed error 分支

graph 执行失败后，仍要回到 orchestrator 统一收口。

## 6. 实施范围

### 6.1 必改文件

#### Query 主链

- `app/workflows/query_orchestrator.py`
- `app/workflows/query_graph.py`
- `tests/test_query_orchestrator.py`
- `tests/test_query_workflow_corrective_rag.py`
- `tests/test_chat_workflow.py`

#### Task 主链

- `app/workflows/tasks/task_orchestrator.py`
- `app/workflows/tasks/document_analysis_graph.py`
- `tests/test_task_service.py`
- `tests/test_task_worker.py`

### 6.2 可能补改文件

- `docs/harnessed-react-agent-redesign-checklist.md`
- `docs/architecture/README.md`

### 6.3 明确不改文件

- `app/harness/**`
- `app/workflows/query_nodes.py`
- `app/workflows/tasks/document_analysis_nodes.py`

## 7. 分阶段实施

### Phase A：先让 Query graph 回到真实执行路径

目标：

- 在 query/chat 主链里恢复 graph invoke
- 保持失败事件、trace、checkpoint、replay 不变

修改重点：

- `QueryWorkflowOrchestrator._run_query_runtime()`
- graph invocation 异常包装
- replay 时基于 checkpoint 的 graph 入口路由

验收：

- query/chat 相关现有单测通过
- 旧 route-loop 执行器不再是 query `langgraph` 路径的执行依赖

### Phase B：再让 Task graph 接回主链

目标：

- `TaskWorkflowOrchestrator._invoke_workflow()` 改走 compiled graph
- 现有 task replay / resume 能继续工作

修改重点：

- task graph 与 checkpointing step 的衔接
- graph 内关键步骤后的 checkpoint 保持现状
- graph 错误统一回流到 orchestrator 收口

验收：

- task service / task worker 相关单测通过
- `document_analysis_graph.py` 从“备用图定义”变成真实主链

### Phase C：清理兼容语义和文档

目标：

- 文档不再声称当前主链是 shared route runtime
- 测试名称与断言改成实际 graph 执行语义

验收：

- 架构文档、测试命名、trace 说明一致

## 8. 关键技术点

### 8.1 Query graph 接口

推荐形态：

- orchestrator 持有一个 `_build_query_app()` 或 `_get_query_app()` 帮助方法
- 内部调用 `build_query_graph(...)`
- graph 使用 `invoke(state)` 作为单次执行入口

关键约束：

- graph 输入输出仍然是 `QueryGraphState`
- orchestrator 仍负责：
  - 初始化 state
  - 执行前 `workflow_started`
  - 执行后 success/failure finalize
  - 持久化 query run

### 8.2 Task graph 接口

推荐形态：

- orchestrator 持有 `_build_task_app()` 或 `_get_task_app()`
- 内部调用 `build_document_analysis_graph(...)`
- graph 输入输出仍然是 `dict[str, Any]` / `DocumentAnalysisState`

关键约束：

- checkpoint 行为不塞进 orchestrator route loop
- 关键步骤完成后的 checkpoint 仍由节点或 graph wrapper 触发

### 8.3 错误处理

graph 执行期异常必须满足：

1. 节点异常先落 node 级 trace / memory
2. orchestrator 能拿到 partial state
3. orchestrator 继续负责：
   - 持久化失败 run
   - 对 stream 返回 `step_failed + error`
   - 对同步接口抛出包装后的 workflow error

### 8.4 Replay / Resume

checkpoint replay 的基本要求：

- checkpoint snapshot 仍保存完整 state
- replay 时仍由 orchestrator 恢复模型对象
- 恢复后的 state 作为 graph 输入
- graph 从 checkpoint 指定的下一节点继续

如果 LangGraph 当前版本对“从中间节点恢复”支持不足，则允许保留：

- 由 orchestrator 通过 graph wrapper 决定入口 route

但不允许重新退回 route loop 主链。

## 9. 风险与控制

### 9.1 最大风险

最大风险不是 graph 本身，而是：

- checkpoint 创建时机变化
- partial state 丢失
- stream 错误事件顺序变化
- task replay 不能从原 next route 继续

### 9.2 控制手段

必须覆盖以下验证：

- query corrective rag 回归
- chat workflow 回归
- query replay / recover 回归
- task service 端到端回归
- task worker 回归
- 失败态 step_failed 事件顺序回归

## 10. 回滚边界

本次改造必须保持可回滚。

可回滚点定义如下：

1. `query` 改造失败时，可单独退回 query route runtime，不影响 task
2. `task` 改造失败时，可单独退回 task route runtime，不影响 query
3. `harness`、tool surface、RagFacade 不应因为本次改造产生回滚耦合

因此代码组织上要避免：

- 一次提交里同时改 workflow graph 与 harness components
- 一次提交里同时改 query/task 两条主链且没有分层隔离

## 11. 一句话结论

本次方案的正确姿势是：

- **只把 workflow 主执行路径切回 LangGraph**
- **不让 LangGraph 侵入 harness runtime 治理层**
- **先 query，后 task，最后清理兼容文档与测试语义**

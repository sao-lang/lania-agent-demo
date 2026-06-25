# TaskSpec 到 Query Graph 收口后续清单

## 1. 背景

这份清单聚焦当前这条改造线：

`TaskSpec -> query graph -> 显式 step runtime`

当前已经完成的部分：

- `query / chat` 已映射到统一 `TaskSpec`
- `check_guardrails` 已提升为显式 step
- query graph 的主步骤顺序已由 `task_spec.steps` 驱动
- `load_session_context / persist_session` 已进入 graph 作为正式节点
- `load_request` 已从 graph 主链移除，初始化前移到 state init / orchestrator
- trace 已开始携带 `task_step_id / task_step_index / completed_step_ids`
- 已明确 `STEP_NODE_IDS / ORCHESTRATION_NODE_IDS`
- 已引入统一 `dispatch_query_step`
- query workflow state 已接入 `TaskRun / step_runtimes / ReflectionDecision / result_contract`
- SSE 已补充 `step_started / step_completed / step_failed` 事件，并与旧 query 事件兼容
- 已在 `check_guardrails / retrieve_evidence / self_reflect` 后创建最小 checkpoint
- 已提供基于 checkpoint 的 replay 入口
- query runtime 已落到可查询的数据面：`TaskRun / RunEvent / Checkpoint`
- query run 已接入 `InMemoryState / SQLiteStateStore`
- 已提供 query run 的 `list / detail / replay / resume / recover / analytics` 读取入口
- 已支持基于持久化 checkpoint 的跨进程恢复入口
- graph 控制流已继续收紧为 `dispatch_query_step + orchestration_next_route`
- 已补充 step/orchestration 节点写入契约校验

这份文档只列这条线后续还能继续做什么，以及建议的推进顺序。

## 2. 当前还没彻底收口的点（更新后）

就这条主线本身来说，核心收口项已经完成。下面剩下的属于下一阶段的平台级扩展，而不是这条 query runtime 主线未闭环。

### 2.1 step 节点和编排节点边界已落地

目前这些节点更像真正的 step：

- `check_guardrails`
- `load_session_context`
- `rewrite_query`
- `expand_queries`
- `lookup_cache`
- `retrieve_evidence`
- `compress_context`
- `grounded_answer`
- `self_reflect`
- `persist_session`

但这些节点更像 orchestration glue：

- `blocked_response`
- `cache_hit_response`
- `retry_retrieve`
- `rewrite_answer`
- `finalize`

目前代码里已经有：

- `STEP_NODE_IDS / ORCHESTRATION_NODE_IDS`
- `dispatch_query_step`
- orchestration node 写入契约校验

### 2.2 step dispatch 与 orchestration route 已落地

目前已经有：

- `dispatch_query_step`
- `route_query_step`

当前已经统一为：

- `dispatch_query_step`
- `route_query_step`
- `orchestration_next_route`
- `route_orchestration`

仍保留的 conditional edges 只负责声明合法跳转集合，不再承载分散的运行语义。

### 2.3 query 进度已接入 `TaskRun`，并已有可恢复数据面

现在 query runtime 已有：

- `current_step_id`
- `completed_step_ids`
- `TaskRun.status`
- `TaskRun.step_attempts`
- `TaskRun.step_runtimes`

目前已经具备：

- `RunEvent`
- `Checkpoint`
- 持久化的 query `TaskRun`

当前已经具备：

- `resume / recover`
- `analytics`
- `list / detail` 查询与筛选

### 2.4 reflection 已收口为统一决策对象，并已进入 replay 数据面

当前 `self_reflect -> retry_retrieve / rewrite_answer / finalize` 已能跑通，且决策结果已收口为 `ReflectionDecision`。

后面如果要继续做 replay / recovery / evaluation，更值得补的是：

- `ReflectionDecision`
- `exit_reason`
- `fallback_action_applied`

### 2.5 SSE、trace、runtime 已共用 step 语义

现在 trace、runtime 与 SSE 已经开始共用 step 语义：

- `workflow_step_started`
- `workflow_step_completed`
- `step_started`
- `step_completed`

当前剩下的已经不是 query 主线内部一致性，而是更高层的平台扩展：

- 更长期的 audit 保留策略
- 更通用的多 workflow 恢复管理器

## 3. 建议的后续事项

### P0. 明确 step 节点 vs orchestration 节点

目标：

- 画清 query graph 中哪些是 `StepSpec` 执行节点
- 画清哪些只是控制流节点
- 禁止再把运行语义偷偷放回 mode 分支或 glue 节点

产出建议：

- 一份节点分类表
- 一组约束规则
- 必要时补充到架构文档

### P0. 引入统一的 `dispatch_query_step`

目标：

- 让 query graph 更像 `document_analysis_graph` 的计划分发模式
- graph 不再需要大量 `route_after_xxx`
- 真正按 `task_spec.steps` 做分发

建议形态：

- `dispatch_query_step`
- `route_query_step`
- 各 step handler 只关心本步骤逻辑

### P0. 把 query 进度接到 `TaskRun`

目标：

- query / chat / task 共享统一运行态语义
- query 不再只是“长得像任务”，而是正式进入平台运行态

至少应接入：

- `current_step_id`
- `completed_step_ids`
- `step_attempts`
- `status`

### P1. 为 step 增加更完整的运行记录

目标：

- 给 replay / recovery / evaluation 留出足够的结构化证据

建议补充字段：

- `attempt_count`
- `started_at / completed_at`
- `exit_reason`
- `fallback_action_applied`
- `degraded`
- `skipped`

### P1. 把 reflection / retry 收口为统一决策对象

目标：

- 不再把 retry / rewrite / accept 只表达为 graph route
- 让后续恢复、回放、评测能直接消费统一决策

建议补充：

- `ReflectionDecision`
- `decision`
- `reason`
- `should_continue`
- `fallback_action`

### P1. 统一 blocked / cache-hit / no-context 的结果契约

目标：

- 避免这些路径继续散落为特殊分支
- 统一 query runtime 的中间结果与最终结果模型

建议覆盖场景：

- `guardrail_blocked`
- `semantic_cache_hit`
- `no_context`
- `corrective_rewrite_applied`

### P2. 让 SSE 跟 step runtime 对齐

目标：

- 让前端 / trace / runtime 看的是同一套步骤语义

建议做法：

- step 级事件作为主语义
- 旧 query 事件作为兼容层保留

### P2. 加最小 checkpoint / replay（已落地）

目标：

- query 这条线先具备最小恢复能力

当前已覆盖三个点：

- `check_guardrails` 后
- `retrieve_evidence` 后
- `self_reflect` 后

当前已提供：

- 基于持久化 checkpoint 的 `resume / recover`

### P2. 继续收紧 `mode` 的职责

目标：

- 保证 `mode` 只表达入口上下文，不再决定步骤语义

允许保留的职责：

- stream / non-stream
- cache namespace
- 运行上下文标记

不应再承载：

- step 顺序
- task runtime 分叉
- query/chat 主语义差异

### P2. 回写架构文档

建议把已经落地的内容回写到：

- `docs/architecture/harnessed-react-agent-redesign.md`

至少更新：

- 已完成项
- 未完成项
- query graph 现状
- 下一批优先事项

## 4. 推荐执行顺序

建议按这个顺序推进：

1. `step 节点 vs orchestration 节点` 边界
2. `dispatch_query_step`
3. `TaskRun` 接入 query runtime
4. `ReflectionDecision / step outcome` 结构化
5. `SSE step 语义`
6. `checkpoint / replay`
7. 文档回写

## 5. 最值得先做的两项

如果只继续做一小步，优先建议：

### 方案 A

- 明确 step 节点和 orchestration 节点边界
- 引入 `dispatch_query_step`

收益：

- 能明显减少 graph 的手写路由噪音
- 后续接 `TaskRun / replay / recovery` 会顺很多

### 方案 B

- 直接把 query runtime 接入 `TaskRun`

收益：

- 更接近平台统一运行时目标
- 但改动面会比方案 A 更大

当前更建议先走方案 A。

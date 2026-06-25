# Harnessed React Agent 重构完成情况清单

## 1. 说明

这份清单根据 [docs/architecture/harnessed-react-agent-redesign.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harnessed-react-agent-redesign.md) 整理，当前重点区分三类状态：

1. 架构主链已落地；
2. 功能已接入主链，但仍需说明边界或补齐闭环；
3. 剩余工程化事项。

后文不再把“已落地”和“彻底没有后续工作”混为一谈，便于后续继续演进、部署和排障。

## 2. P0 完成情况

以下 P0 项已完成：

- [x] 平台化接入 `Memory Harness / Reflection Harness / Recovery Manager`
  - 已新增并接入 `ReflectionHarness / RecoveryManager`，query runtime 不再把这两部分逻辑散在节点与 orchestrator 中。

- [x] 落地 `ContextBundle / Checkpoint` 等核心契约
  - `ContextBundle` 已进入 query runtime 主链并进入 checkpoint snapshot；`Checkpoint` 继续作为 replay / resume / recover 的统一入口。

- [x] 在 step 级完成至少一个明确的局部 `ReAct`
  - `retrieve_evidence` 已改为 `StepSpec -> ContextBundle -> BoundedLocalReActRuntime -> ExecutionHarness` 的受控执行路径。

- [x] 为高风险工具补齐 `Tool Sandbox` 基础能力
  - 已新增 `ToolSandbox`，工具 schema 具备 `risk_level / sandbox_mode`，并将 `finalize_report` 纳入高风险沙盒约束。

当前建议：后续跟进已从 “补 P0” 切换为 “推进 P1”。

## 3. 统一运行时与状态主链（P1 主要欠账）

- [x] 让 `query / chat / task` 真正共用同一套 `step runtime`
  - 现状：`query / chat / task` 已统一到 `TaskSpec / TaskRun / StepRuntimeRecord / Checkpoint / RunEvent` 主链，并共用受控 step lifecycle；当前保留的是首个领域 skill 的实现差异，而不是运行时契约差异。
  - 依据：原文第 2.4、14.2、20.2 节。

- [x] 将现有专用 task flow 下沉为 skill
  - 现状：已新增 `TaskSkill / TaskSkillRegistry`，并将 `document_analysis` 显式注册为首个领域 skill；原工作流文件保留，但语义上不再作为平台默认主链。
  - 依据：原文第 2.4、20.2、21 节。

- [x] 建立统一的 `checkpoint / rollback / replay / resume` runtime 能力
  - 现状：`query` 原有能力继续保留；`document_analysis task` 已补齐 `checkpoint / replay / resume`，checkpoint snapshot 进入 task run 主链并可通过 API 查询与恢复。
  - 依据：原文第 2.4、14.2、20.2 节。

- [x] 形成可查询、可回放的 `ContextBundle / Checkpoint / RunEvent` 主链
  - 现状：`document_analysis task` 已将 `ContextBundle / Checkpoint / RunEvent` 全量接入 `TaskRun` 与持久化记录，query/task 均可查询回放。
  - 依据：原文第 2.4、20.2 节。

- [x] 让 `TaskRun` 与 `RunEvent` 成为统一可查询数据面
  - 现状：已新增 `task_runs` 持久化桶与 `/tasks/runs` 查询、详情、replay、resume API；task/query 两侧都具备独立 run 级数据面。
  - 依据：原文第 20.2 节。

- [x] 让 `artifact-first` 输出成为主链
  - 现状：task 侧最终结果已以 artifact 主链为主；query/chat 侧已新增 `result_artifact` 并写入 `result_contract` 与 run data plane，最终交付不再只有 answer 文本。当前 `result_contract` 已收口为统一强类型模型，并继续兼容存量扩展键；同时已新增独立 `ArtifactCapability`，补齐 `/api/v1/artifacts/*`、health 可见性以及 `list_artifacts / read_artifact` 两个默认 task tools。
  - 依据：原文第 20.2、21 节。

## 4. 契约与模型补齐项

- [x] 新建并接入 `ContextBundle / Checkpoint`
  - 现状：`ContextBundle` 已进 query 主链与 checkpoint snapshot；`Checkpoint` 已进入 replay / resume / recover 运行面。

- [x] `MemoryRecord` 独立契约
  - 现状：已新增 `MemoryRecord`，并通过 adapter 将 `TaskMemoryEntry / ArtifactMemoryEntry / ReflectionEntry / QueryRunEvent` 投影为统一 memory records，进入 task/query runtime 详情与持久化数据面。

- [x] 新建 `PromptSpec / PromptBuildRequest / PromptBuildResult`
  - 现状：已新增统一 prompt contracts，并将 task 侧 `PromptBuilder` 渲染结果与 query 侧 grounded answer prompt 接入 `prompt_specs / prompt_build_requests / prompt_build_results` 主链。
  - 依据：原文第 24.4 节。

- [x] 补齐 `EvidencePack / RetrievalQualityReport / GroundedContext / GraphSubgraph`
  - 现状：`EvidencePack / RetrievalQualityReport` 继续复用知识能力契约；已新增并接入 `GroundedContext / GraphSubgraph`，task/query 两侧都会在 evidence 与 answer 主链上产出统一 grounded 视图。
  - 依据：原文第 24.4 节。

- [x] 用 adapter 将旧 `query / task` 流程彻底接到新模型
  - 现状：已新增 runtime contract adapters，并把旧 `query / chat / task` 运行态、prompt 渲染、memory entries、evidence / citations 全部投影到统一 contracts；旧模型继续保留为兼容层。
  - 依据：原文第 24.4 节。

### 4.1 当前建议视为稳定的核心契约

后续新增能力时，建议优先复用下列契约，不再重新发明平行模型：

- `TaskSpec / TaskRun / StepSpec / RunBudget`
- `ContextBundle / Checkpoint / RunEvent`
- `PromptSpec / PromptBuildRequest / PromptBuildResult`
- `MemoryRecord`
- `EvidencePack / RetrievalQualityReport / GroundedContext / GraphSubgraph`
- `Artifact`
- `Result Contract`
- `SubAgentHandoff`

补充说明：

- `Artifact` 主链当前可视为稳定；
- `Result Contract` 已进入 task/query 数据面并完成强类型收口，同时保留兼容扩展字段以承接存量流程中的补充键。

### 4.2 当前建议视为稳定的能力接口

后续 capability / provider / worker 化时，建议继续收口到下列稳定接口：

- `KnowledgeCapability`
- `TaskSkillRegistry`
- `ModelRouter`
- `ToolSandbox`
- `/api/v1/knowledge/*`
- `/api/v1/sandbox/execute-tool`

## 5. 后续深化项（P2）

- [x] 高风险能力的强隔离执行
  - 现状：`ToolSandbox` 已支持 `process_isolated` 与 `remote_http` 两类执行 provider，高风险工具 `finalize_report` 可走本机独立进程 worker，也可切到远程 sandbox worker API；`sandbox_mode` 会进入 trace 与 execution runtime summary，部署侧可继续扩展为独立 worker 池。

- [x] `Knowledge Capability` 服务化
  - 现状：已新增 capability 独立 API 面 `/api/v1/knowledge/document-context|search|grounded-answer`，并补齐 `remote_http` provider；query/task/execution 继续依赖统一 `KnowledgeCapability` 接口，可切到远程服务实例而不改 runtime 主链，满足“可独立演进、可独立部署”的 P2 目标。

- [x] 模型路由与成本调度
  - 现状：已新增 `ModelRouter`，`task_analysis / task_review / knowledge_answer / knowledge_check / knowledge_rewrite / json_repair` 在进入 LLM 前统一走 route 决策，并记录 `profile / estimated_cost_units`。

- [x] 新 capability / skill 接入不改 runtime 骨架
  - 现状：已新增 `build_default_task_skill_registry` 与 `KnowledgeCapabilityRegistry`，新增 builtin skill/provider 时不再需要改 `TaskWorkflowOrchestrator / ExecutionHarness` 的主骨架。

- [x] 受控子代理稳定 handoff 与审计记录
  - 现状：已新增 `SubAgentHandoff` 契约，task run 会持久化 `handoff_id / source_step_id / context_keys / step_limit / budget_limit / sandbox_profile`，并进入 trace + memory 审计链路。

### 5.1 P2 完成后的当前判断

从代码落地角度看，原设计文档列出的 P0 / P1 / P2 事项已经大体具备对应实现，架构主链可以视为已落地；此前识别出的几项代码闭环欠账也已补齐，当前剩余重点已主要转为工程化与后续扩展验证。

当前剩余内容主要分为四类：

1. 工程化事项：
   `Knowledge Capability` 与 `Sandbox Worker` 的独立部署单元、发布方式、健康检查、扩缩容策略。
2. 生产治理：
   认证鉴权、跨服务超时、熔断、限流、告警、成本看板。
3. 能力扩展：
   新增更多 capability / skill / sub-agent，并验证现有 runtime 骨架不需要再改。

## 6. 运行配置补充

### 6.1 Knowledge Capability 远程化

启用远程 knowledge provider 时，当前使用以下配置：

- `KNOWLEDGE_CAPABILITY_PROVIDER=remote_http`
- `KNOWLEDGE_CAPABILITY_BASE_URL`
- `KNOWLEDGE_CAPABILITY_TIMEOUT_SECONDS`
- `KNOWLEDGE_CAPABILITY_AUTH_TOKEN`

### 6.2 Sandbox Worker 远程化

启用远程 sandbox executor 时，当前使用以下配置：

- `SANDBOX_EXECUTOR_PROVIDER=remote_http`
- `SANDBOX_EXECUTOR_BASE_URL`
- `SANDBOX_EXECUTOR_TIMEOUT_SECONDS`
- `SANDBOX_EXECUTOR_AUTH_TOKEN`

### 6.3 默认本地模式

在未显式切换 provider 时，当前默认行为为：

- knowledge capability 使用本地 `default` provider
- 高风险 tool 使用本机 `process_isolated` worker
- query/task 主链不需要感知本地与远程 provider 差异

## 7. 已落地但仍需说明边界的功能项

以下事项不再表示“主链没做完”，而是表示“已经落地，但仍需明确当前边界、兼容层或剩余闭环项”：

- [x] `query / chat / task` 真正收敛到同一套运行时实现
  - 现状：`query / chat` 与 `task/document_analysis` 已统一到同一套 `TaskSpec / TaskRun / StepRuntimeRecord / Checkpoint / RunEvent` 运行时契约，但 **workflow 编排主链已经切回 LangGraph compiled graph**。当前 query/chat 会真实调用 `build_query_graph(...).invoke(...)`，`TaskWorkflowOrchestrator` 也会真实调用 `build_document_analysis_graph(...).invoke(...)`；共享的 `step_lifecycle`、checkpoint、run event 和持久化能力继续保留为统一运行时基础设施，原 route-loop 兼容实现已下线。

- [x] `document_analysis` 彻底从专用 workflow 演进为通用 skill 样板
  - 现状：已新增 `StructuredDocumentSkill + BuiltinTaskSkillSpec` 通用样板，`document_analysis / document_summary` 通过同一套 skill 配置与 state 初始化接入，不再需要为新增同类 skill 改动 orchestrator 主循环；`document_summary` 现已具备独立 `artifact_type/result_contract/title` 语义。

- [x] 新增至少一个非 `document_analysis` 的内建领域 skill
  - 现状：已新增并注册 `document_summary` 内建 skill；默认 skill registry 现同时包含 `document_analysis / document_summary`，用于验证新增 skill 不需要改 runtime 骨架，且 `document_summary` 已补齐独立 artifact/result contract 样板验证。

- [x] `TaskRequest` 从单一 task type 扩展为更通用的 task surface
  - 现状：`TaskRequest.task_type` 已改为通用字符串类型，新增通用 `POST /api/v1/tasks` 创建入口，并在 service 层基于 `TaskSkillRegistry` 校验可支持的 task type；原 `/tasks/document-analysis` 入口保留为兼容别名。

- [x] 统一 runtime 的公共 step lifecycle 继续下沉
  - 现状：已新增共享 `app/workflows/step_lifecycle.py`，并把 query/task 两侧的 `step started / completed / failed / checkpoint / run event` 核心运行态收口到同一套 helper；query replay/checkpoint 侧的 `step_runtimes` 也已统一做强类型 normalize/dump，不再在持久化时回落为裸字典。

- [x] 更多通用 capability 落地
  - 现状：除 `KnowledgeCapability` 外，已新增 `RepositoryCapability / ApiContractCapability / DatabaseCapability / ArtifactCapability`，均具备稳定契约、本地 provider、独立 `/api/v1/*` 能力面、主 `/health` 可见性，以及对应的默认 task tools；当前数据库 capability 默认提供 `list_database_tables / describe_database_table / query_database` 三个只读工具，artifact capability 默认提供 `list_artifacts / read_artifact` 两个只读工具，继续验证了 capability/provider 模式可以在不改 runtime 骨架的情况下持续复用。

- [x] 更多受控 sub-agent 落地
  - 现状：已形成 `evidence_agent / reporting_agent / review_agent` 三类内建受控子代理，分别覆盖检索补证据、草稿起草、审查修订，并统一进入 `SubAgentHandoff`、trace 与 memory 审计链路。

- [x] `Sandbox Worker` 扩展为更完整的高风险工具执行池
  - 现状：已新增 `SandboxWorkerRegistry` 与 `/api/v1/sandbox/tools|tools/{tool_name}|health` 数据面，worker 不再硬编码单一 `finalize_report`；当前默认支持 `draft_report / review_report / finalize_report` 三个可注册隔离工具，并可按 schema 查询与扩展。本地 `process_isolated` 执行路径现已改为使用实例级注入的 registry，避免回落到全局默认 registry。

- [x] `ModelRouter` 接入真实成本统计回写
  - 现状：`ModelRouter` 已新增实际消费回写链路，优先读取 provider usage / cost，缺失时回退到 token 估算；`model_route_selected / model_route_consumed` 已进入 trace、`/health`、`/metrics` 与 task workflow cost 聚合，不再只有静态 `estimated_cost_units`。

- [x] 远程 `knowledge` / `sandbox` provider 的运维能力补齐
  - 现状：已补充 `/api/v1/knowledge/health`、`/api/v1/sandbox/health`、主 `/health` 中的 remote worker readiness/config data plane，并在远程 provider 中接入鉴权失败分类、超时/限流/上游故障 fallback、本地降级开关、基础断路器以及统一运维手册 `docs/operations/remote-provider-runbook.md`。主 `/health` 现已同时包含配置态 readiness 与主动远程探测结果（状态码、延迟、错误信息），`/metrics` 也已暴露压平后的 remote worker probe 指标，便于接告警与看板。

## 8. 剩余建议项

以下事项不再属于“架构未落地”，但建议尽快补齐：

1. 当前已新增 `RepositoryCapability / ApiContractCapability / DatabaseCapability`；后续可继续补更多 provider（如远程数据库或受限网关 provider），进一步验证 capability/provider 模式的跨后端复用能力。
2. 当前已新增 `RepositoryCapability / ApiContractCapability / DatabaseCapability / ArtifactCapability`；后续可继续补更多 provider（如远程数据库、受限网关或 artifact 存储 provider），进一步验证 capability/provider 模式的跨后端复用能力。
3. 当前已新增 `ApiContractCapability`、`list_api_contracts / search_api_contract_operations / read_api_contract` 三个 task tools，以及 `contract_agent` 受控子代理；后续仍可继续增加更多内建 skill/sub-agent，但 runtime 主骨架无需改动这一点已再次得到验证。
4. 远程 worker 的告警、看板与发布巡检链路仍可继续补齐，但这项当前不再作为核心能力演进的优先方向。

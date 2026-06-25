# 通用 Harnessed Agent 平台重构设计

> 文档角色：历史源文档 / 顶层背景文档
>  
> 是否建议默认加载：否
>  
> 适合回答的问题：
> - 最初为什么要从 `RAG 应用` 走向 `Harnessed Agent Runtime`
> - 顶层演进判断和早期优先级是什么
> - 早期设计决策的背景依据是什么
>  
> 建议搭配阅读：
> - 默认先读 `README.md`
> - 实施时优先读 `harness-composition-refactor-plan.md`

## 1. 文档目的

本文收口当前项目下一阶段最重要的架构决策，并按实施优先级重新组织内容，回答四个问题：

- 当前项目为什么不能继续以 `RAG 应用` 定义
- `RAG` 应该如何剥离并下沉为能力
- `Harness-first` 的运行时应该如何分层
- 局部 `ReAct`、记忆污染、死循环、恢复、沙盒应如何统一设计

本文优先服务实施，不再追求覆盖所有演化细节。

## 2. 执行摘要

### 2.1 当前判断

当前项目已经不是一个单纯的问答型 `RAG` 服务，更接近：

- 带任务系统的专用 agent 原型
- 具备治理雏形的 `Harnessed Runtime`
- 带受控子代理能力的任务型后端

### 2.2 下一阶段目标

项目建议演进为：

```text
Task / Session / Query
  -> Harness Runtime
  -> Agent Runtime
  -> Bounded Local ReAct
  -> Tool / Capability / Skill
  -> Artifact / Audit / Evaluation
```

核心结论：

- `Harness` 是系统顶层，不是附属能力
- `RAG` 是 `Knowledge Capability`，不再是产品主轴
- `ReAct` 只用于 step 内局部执行，不接管系统治理
- 系统主对象应从 `Query` 转向 `Task + Tool + Artifact`
- 当前阶段坚持 `单主 agent + 少量受控 sub-agent`

### 2.3 实施优先级

#### P0

- `RAG -> Knowledge Capability` 边界落定
- `TaskSpec / TaskRun / StepSpec` 建模
- `Harness-first` 分层落地
- `Memory / Reflection / Recovery` 补齐
- 第一版局部 `ReAct`

#### P1

- `query / chat / task` 统一到同一任务运行时
- 现有专用流程下沉为独立 skill
- checkpoint / rollback / replay
- artifact-first 输出体系

#### P2

- `Sandbox Harness` 深化
- `Knowledge Capability` 服务化
- 更多通用 capability 与更多受控子代理
- 模型路由、成本调度、预取优化

### 2.4 当前实现进展（截至 2026-06-12）

当前代码已经完成的部分：

- 已建立平台层 `TaskSpec / TaskRun / StepSpec / RunBudget`
- 已把 `RAG` 主路径收口为 `Knowledge Capability`
- 已新增 `grounded_answer` 工具，并让知识工具统一走 capability
- classic `query / chat / stream_query / stream_chat` 的检索与 grounded answer 已优先复用 `Knowledge Capability`
- `corrective_rag` 的策略对象与质量报告已下沉到 `capabilities/knowledge/contracts.py`
- `query / chat` 已可通过 adapter 映射为统一 `TaskSpec`
- LangGraph query workflow state 已开始携带 `task_spec`

仍未完成的部分：

- `query / chat / task` 还没有真正共用同一套 step runtime，只是先完成了 `TaskSpec` 投影
- `document_analysis` 仍然是专用 workflow，还未完全下沉为独立 skill
- artifact-first 输出还没有成为默认主链

补充更新（截至 2026-06-16）：

- query runtime 已接入 `ContextBundle`，并把 `context_bundles` 写入 checkpoint snapshot
- 已新增 `ReflectionHarness / RecoveryManager / ToolSandbox`
- `retrieve_evidence` 已接入 `BoundedLocalReActRuntime + ExecutionHarness`
- 高风险工具已具备基础沙盒约束，`finalize_report` 已声明为高风险工具
- query 的 checkpoint / replay / resume / recover 维持可用，P0 所需的恢复主链已收口

## 3. 当前问题与改造方向

### 3.1 当前系统已具备的基础

当前代码已经具备：

- 较完整的 `RAG` 能力栈
- `Task Service` 与 `Task Worker`
- `Tool Registry`
- 专用任务 workflow
- `Context / Execution / Guardrail / Policy / Evaluation Harness`
- 任务级 `artifact / memory / trace`
- `Sub-Agent Runtime`

### 3.2 当前最核心的问题

问题不在于“能力不够多”，而在于“边界不够清晰”：

- `RAG` 同时承担检索、回答、证据底座等多重职责
- 查询侧与任务侧是两套主路径，未统一为同一个 runtime
- 治理逻辑散在 workflow、tool、review、prompt helper 中
- 当前 agent 仍偏专用流程，还不是稳定执行内核
- 状态、记忆、回放、恢复仍未成为平台级能力

### 3.3 这次重构要解决什么

本次重构要把系统从：

```text
RAG + Query + Task Workflow
```

改造成：

```text
Harness Runtime
  -> Agent Runtime
  -> Bounded Local ReAct
  -> Capability Surface
  -> Artifact / Audit / Evaluation
```

## 4. 顶层设计原则

### 4.1 Harness 优先于 Agent 自治

所有预算、权限、策略、恢复、审计、评测，都必须由 `Harness` 控制。

### 4.2 ReAct 只用于局部执行

`ReAct` 只解决“当前 step 下一动作是什么”，不负责系统顶层放权。

### 4.3 RAG 是能力，不是产品定义

`RAG` 提供证据、上下文和 grounding，但不再承担产品中心角色。

### 4.4 Artifact 优先于 Answer

系统最终交付应是结构化 `artifact`，而不是仅有回答文本。

### 4.5 优先可控，再谈开放

优先保证：

- 有界步骤数
- 有界工具集
- 有界预算
- 有界 fallback
- 有界输出格式

## 5. 目标架构

### 5.1 主从关系

```text
Harness > Agent > ReAct > Tool
```

职责含义：

- `Harness` 决定能不能做、做到什么程度、出了问题如何处理
- `Agent` 决定任务如何推进
- `ReAct` 决定某一步里如何观察与选择动作
- `Tool` 只负责稳定暴露能力

### 5.2 统一运行骨架

```text
Unified Entry
  -> TaskSpec
  -> Harness Precheck
  -> Agent Runtime
  -> Bounded ReAct Step Runtime
  -> Tool / Skill / Sub-Agent
  -> Grounding / Artifact Commit
  -> Evaluation / Trace / Audit
```

目标状态下：

- `query` 是轻量任务
- `chat` 是带 session 的连续任务
- 任何领域任务都应映射为统一 `TaskSpec`

### 5.3 Harness 分层

#### Governance Harness

负责：

- policy
- permission
- budget
- guardrail
- sandbox
- evaluation
- audit

#### Runtime Harness

负责：

- workflow orchestration
- bounded react step runtime
- execution
- reflection
- recovery
- sub-agent handoff

#### State Harness

负责：

- context slice
- memory layers
- artifact lineage
- checkpoint / rollback
- replay / resume

#### Capability Harness

负责：

- tool registry
- capability routing
- external tool adapters
- tool contract validation

## 6. RAG 剥离与能力化重构

### 6.1 为什么必须剥离

当前 `RAG` 同时承担：

- 文档摄取与索引
- 检索与图增强召回
- 查询回答生成
- 任务侧证据工具底座

带来的问题：

- 查询链路和任务链路都直接绑定 `Rag*` 实现
- `collection_name / doc_ids / vector_store / retrieval` 这些领域词渗透到任务运行时
- 后续接入代码、仓库、数据库、API 合同能力时，系统会继续围绕 `RAG` 扩张

### 6.2 剥离后的边界

#### 平台内核负责

- task / run / session 生命周期
- planner / workflow / bounded react
- tool execution / reflection / recovery
- memory / checkpoint / audit
- policy / guardrail / sandbox / evaluation

#### Knowledge Capability 负责

- 文档导入与索引
- chunk / embedding / rerank / graph retrieval
- evidence retrieval
- grounded answer
- citation / evidence pack 标准化输出

#### Skill 负责

- 何时调用能力
- 如何组织 evidence、analysis、artifact
- 定义 success criteria 与 fallback 规则

### 6.3 推荐拆分顺序

#### Step 1. 依赖反转

先定义抽象接口：

- `KnowledgeProvider`
- `KnowledgeIndexer`
- `EvidenceRetriever`
- `GroundedAnswerProvider`

#### Step 2. 工具收口

统一知识工具：

- `load_document_context`
- `retrieve_evidence`
- `retrieve_graph_evidence`
- `grounded_answer`

#### Step 3. 任务模型拆分

- 平台层：`TaskSpec / TaskRun / StepSpec / RunBudget`
- 技能层：`SkillInput`、`GroundedQueryInput`、`StructuredAnalysisInput`

#### Step 4. API 分层

- 平台入口：`Task / Run / Event / Artifact`
- knowledge 入口：`document / index / search / answer`

### 6.4 长期目录形态

```text
app/
  kernel/
  capabilities/
    knowledge/
    engineering/
    artifact/
  skills/
    generic_analysis/
    grounded_query/
    session_chat/
    structured_report/
```

### 6.5 Knowledge Capability 增强路线：CRAG + Self-RAG + GraphRAG

这三类能力可以做，而且值得做，但应明确放在 `Knowledge Capability` 内部，而不是重新把平台定义成“高级 RAG 系统”。

推荐关系如下：

```text
General-Purpose Agent Runtime
  -> Knowledge Capability
       -> Hybrid Retrieval
       -> CRAG
       -> GraphRAG
       -> Bounded Self-RAG
```

核心原则：

- `Agent Runtime` 是主轴
- `Knowledge Capability` 是能力层
- `CRAG / GraphRAG / Self-RAG` 是能力层增强策略

#### 三者分别解决什么问题

##### CRAG

- 用于检索结果质量评估与纠错
- 解决“召回结果不准、不全、不稳定”的问题
- 适合作为 `retrieval -> quality check -> rewrite / reroute / fail-with-gap` 的中间层

##### GraphRAG

- 用于图结构知识检索、多跳关系推理、拓扑与依赖分析
- 解决“实体关系、跨模块调用链、架构依赖追踪”这类问题
- 适合技术架构审计、服务依赖分析、复杂文档实体网络场景

##### Self-RAG

- 用于生成阶段的按需再检索与证据充足性判断
- 解决“当前证据够不够、要不要继续查”的问题
- 不应做成开放式自由循环，而应做成 `bounded local ReAct` 的一部分

#### 推荐的模块拆分

```text
capabilities/knowledge/
  contracts/
  indexing/
  retrieval/
  quality/
  reasoning/
  graph/
  orchestration/
```

对应职责：

- `retrieval/`
  - dense retrieval
  - lexical retrieval
  - hybrid retrieval
  - rerank
- `quality/`
  - CRAG evaluator
  - retrieval quality scoring
  - query rewrite
  - fallback routing
- `reasoning/`
  - self-rag retrieval trigger
  - evidence sufficiency judge
  - grounding / citation check
- `graph/`
  - entity / relation extraction
  - graph index
  - graph traversal retrieval
  - subgraph builder
- `orchestration/`
  - 知识查询执行链路编排
  - route 到 normal / crag / graph / self-rag 路径

#### 推荐执行链路

上层 skill 不应直接感知 `CRAG / GraphRAG / Self-RAG` 的细节，而应只依赖稳定接口：

- `knowledge.search()`
- `knowledge.build_context()`
- `knowledge.evaluate_retrieval()`
- `knowledge.graph_search()`

内部执行链路建议为：

```text
query
  -> retrieval
  -> retrieval quality check (CRAG)
  -> if low quality: rewrite / reroute / graph route
  -> if step still lacks evidence: bounded self-rag loop
  -> build grounded context / evidence pack
```

#### 为什么不建议三者同时硬耦合上线

- `GraphRAG` 成本最高，前期不一定带来最大收益
- `Self-RAG` 最容易引入循环、成本失控与记忆污染
- `CRAG` 最容易先带来质量提升与可解释性
- 如果基础 retrieval / rerank / evidence contract 还不稳定，三者同时接入会把能力层耦死

#### 建议的演进顺序

##### P0

- 先固定 `RAG -> Knowledge Capability` 边界
- 做好 `hybrid retrieval + rerank`
- 建立 `EvidencePack / GroundedContext / RetrievalQualityReport` 契约

##### P1

- 引入 `CRAG`
- 增加 retrieval quality scoring
- 支持 query rewrite、fallback retriever、fail-with-gap

##### P2

- 引入 `GraphRAG`
- 优先面向架构依赖、服务调用关系、实体关系追踪场景

##### P3

- 引入 `Bounded Self-RAG`
- 只接入 `retrieve_evidence / analysis_refine / draft_review_fix` 这类局部步骤
- 必须受 `StepSpec + Context Harness + Reflection Harness + Budget` 共同约束

#### 建议新增的数据契约

```python
class EvidencePack(BaseModel):
    query: str
    items: list[dict[str, Any]]
    citations: list[dict[str, Any]] = []
    coverage_score: float | None = None
    relevance_score: float | None = None


class RetrievalQualityReport(BaseModel):
    query: str
    overall_score: float
    coverage_score: float
    relevance_score: float
    confidence_score: float
    suggested_actions: list[str] = []


class GroundedContext(BaseModel):
    objective: str
    evidence_pack_ref: str
    grounded_facts: list[dict[str, Any]]
    unresolved_gaps: list[str] = []


class GraphSubgraph(BaseModel):
    root_entities: list[str]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
```

#### 最终定位

`CRAG + GraphRAG + Self-RAG` 应被定义为：

- `Knowledge Capability Strategy`
- 不是顶层平台定义
- 不是新的系统主轴
- 也不是 skill 层直接操控的底层细节

平台层最终暴露的是稳定知识能力；至于内部具体采用 `CRAG`、`GraphRAG` 还是受控 `Self-RAG`，应由 knowledge orchestration 决定。

## 7. 模块重构方案

### 7.1 可以直接复用并升级的模块

- `app/harness/context.py`
- `app/harness/execution.py`
- `app/harness/policy.py`
- `app/harness/guardrails.py`
- `app/harness/evaluation.py`
- `app/harness/selection.py`
- `app/harness/compression.py`
- `app/harness/grounding.py`
- `app/agents/runtime.py`
- `app/agents/subagents.py`
- `app/agents/tools/base.py`
- `app/agents/tools/registry.py`

判断：

- 这些模块已经形成 `Harness-first` 雏形
- 建议继续演进，不建议推倒重写

### 7.2 需要下沉为 Skill 或 Capability 的模块

#### 下沉为 Skill

- `app/agents/planner.py`
- `app/workflows/tasks/task_orchestrator.py`
- `app/workflows/tasks/document_analysis_graph.py`
- `app/workflows/tasks/document_analysis_nodes.py`

这些模块在现阶段仍然可以先保留文件名，但语义上应被视为“首个领域 skill 的实现”，而不是平台默认主链。

#### 下沉为 Knowledge Capability

- `app/rag/*`
- `app/agents/tools/knowledge_tools.py` 中与 RAG 强耦合的逻辑

### 7.3 需要新增的核心模块

- `app/harness/memory.py`
- `app/harness/reflection.py`
- `app/harness/recovery.py`
- `app/harness/sandbox.py`
- `app/harness/react_runtime.py`
- `app/harness/checkpoint.py`
- `app/harness/events.py`

### 7.4 需要统一的核心对象

#### 平台层

- `TaskSpec`
- `TaskRun`
- `StepSpec`
- `RunBudget`
- `RunEvent`
- `Checkpoint`

#### 运行态

- `ContextBundle`
- `ReActState`
- `GroundingBundle`
- `Artifact`

#### 技能层

- `SkillInput`
- `GroundedQueryInput`
- `SessionChatInput`
- `StructuredAnalysisInput`

## 8. 局部 ReAct 设计

### 8.1 设计目标

项目应采用 `bounded local ReAct`，而不是开放式 autonomous loop。

约束如下：

- ReAct 只在单个 step 内运行
- 只解决“当前这一步下一动作是什么”
- 只能使用 Harness 允许的上下文和工具
- 只能运行有限轮数
- 结果必须回收到 state / memory / artifact

### 8.2 与 Planner、Workflow 的边界

#### Planner 负责

- 任务拆解
- step 定义
- candidate tools
- success criteria
- fallback 规则

#### Workflow 负责

- 步骤顺序
- 状态迁移
- step 生命周期

#### ReAct 负责

- 当前 step 内的动作选择
- 工具执行后的局部观察与停止判断
- 有限范围内的补证据、修订、切换工具

### 8.3 标准步内流程

```text
load step objective
  -> inspect context bundle
  -> select next action
  -> execute tool / sub-agent
  -> collect observation
  -> local reflection
  -> continue / finish / fallback / abort
```

### 8.4 Step Contract

每个支持 ReAct 的步骤应定义：

- `step_id`
- `objective`
- `allowed_tools`
- `max_turns`
- `success_criteria`
- `stop_conditions`
- `fallback_action`
- `output_schema`

### 8.5 第一批优先落地场景

#### 1. 检索步 ReAct

- 决定普通检索还是 graph 检索
- 判断证据是否足够

#### 2. Draft-Review-Revise ReAct

- review 不通过后做有限轮修订
- 在 unsupported claim、missing section、格式问题之间做有界修复

## 9. 稳定性设计

### 9.1 总体目标

稳定性不是“永不失败”，而是：

- 失败可控
- 失败可解释
- 失败可恢复
- 失败后不污染后续运行态

### 9.2 记忆稳定性原则

记忆污染不再单独在稳定性章节展开，而是统一收口到 `Memory Harness` 章节。

这里先保留 4 条总原则：

- `thought` 不进入长期记忆
- tool output 先写 staging，不直接 commit
- fallback 结果必须带 `degraded` 标签
- 新记忆与旧记忆冲突时标记 conflict，不直接覆盖

### 9.3 死循环防护

至少需要：

- `step max_turns`
- 重复动作检测
- 无增益检测
- 状态回访检测
- Reflection 停机器

建议持续追踪：

- `coverage_delta`
- `artifact_delta`
- `new_evidence_count`
- `unsupported_claim_delta`

### 9.4 报错恢复

#### 错误分类

- `transient_error`
- `tool_input_error`
- `permission_error`
- `policy_error`
- `quality_error`
- `logic_loop_error`

#### 恢复矩阵

```text
transient_error   -> retry_same_action
tool_input_error  -> revise_payload / fallback
permission_error  -> abort
policy_error      -> abort
quality_error     -> continue_with_gap / replan
logic_loop_error  -> fallback / abort
```

#### 恢复层级

- tool 级恢复
- step 级恢复
- run 级恢复

#### 两阶段提交

```text
prepare
  -> execute
  -> validate
  -> commit
```

### 9.5 需要新增的稳定性模块

- `Memory Harness`
- `Reflection Harness`
- `Recovery Manager`
- `Snapshot Manager`
- `Loop Detector`
- `Run Health Monitor`

## 10. Context Harness 设计

### 10.1 设计目标

`Context Harness` 的目标不是“尽量多给模型信息”，而是：

- 让当前 step 拿到最小必要工作集
- 控制上下文长度与 token 成本
- 保证进入上下文的信息尽可能可靠
- 避免脏状态、低置信推断和过期内容污染推理

一句话总结：

```text
少而准
```

### 10.2 Context 的来源

`ContextBundle` 不应直接来自单一来源，而应来自多个来源的受控聚合。

建议的标准来源：

- `run state`
- `step history`
- `memory layers`
- `artifact lineage`
- `evidence / citations`
- `session context`
- `policy-injected constraints`

建议把 context source 明确定义为：

```text
Context Sources
  -> state
  -> memory
  -> artifact
  -> evidence
  -> session
  -> policy constraints
```

### 10.3 Context 为什么会过长

context 变长通常来自以下错误做法：

- 注入全量历史对话
- 注入全量 task state
- 注入全量 tool outputs
- 注入全量 memory
- 注入过多 evidence 片段
- 注入完整 artifact 全文，而不是当前 section

因此必须明确：

- `context` 不是历史归档
- `context` 是当前 step 的最小必要工作集

### 10.4 长度控制策略

#### 1. 分层预算

不同 context slice 必须有独立预算，而不是共享一个模糊的总长度。

建议至少区分：

- `objective`
- `state_slice`
- `evidence_slice`
- `artifact_slice`
- `memory_slice`
- `tool_options`

#### 2. 先选再压

流程必须是：

```text
collect candidates
  -> select
  -> rank
  -> compress
  -> budget trim
  -> package
```

不要直接把 top-k 全量拼接。

#### 3. 目标驱动裁剪

每个 step 都必须有明确 `objective`。

所有候选上下文都要回答一个问题：

- 它是否直接帮助当前 step objective？

不能回答的内容不进入上下文。

#### 4. 摘要优先于原文

以下内容优先使用结构化摘要，而不是原文堆叠：

- tool output
- review notes
- memory entries
- previous step outcome

#### 5. 硬预算而不是软建议

`Context Harness` 应基于硬性 token budget 工作：

- 先分配总预算
- 再分配各 slice 预算
- 超预算时按优先级裁剪
- 记录被裁掉的原因

### 10.5 可靠性控制策略

### 10.5.1 基本原则

上下文可靠性比上下文长度更重要。

必须明确：

- `thought` 不等于事实
- `reflection` 不等于事实
- 失败 tool output 不等于事实

### 10.5.2 置信等级

建议所有可进入 context 的片段都携带：

- `source`
- `trust_level`
- `created_at`
- `scope`
- `grounded`
- `stale`

最低建议区分：

- `unverified`
- `provisional`
- `verified`
- `final`

### 10.5.3 Grounding 与 Commit Gate

进入高优先级 context 的内容应优先来自：

- 已验证 tool output
- 已完成步骤摘要
- 已有 evidence map 的结论
- 已通过 review 的 artifact 片段

而不应优先来自：

- 临时 thought
- 失败分支中的推测
- 未标记降级的 fallback 输出

建议统一经过：

```text
raw output
  -> validation
  -> grounding / trust assignment
  -> memory commit gate
  -> high-priority context candidate
```

### 10.5.4 新鲜度控制

内容可能是真的，但可能已经过期。

因此每条上下文记录建议带：

- `created_at`
- `step_id`
- `version`
- `checkpoint_ref`

以下内容默认降权或剔除：

- 来自旧 plan 版本
- 来自回滚前状态
- 已被后续结论否定
- 来自旧 artifact 版本

### 10.6 Context 与 Memory 的边界

必须区分：

```text
memory = 仓库
context = 当前工作台
```

含义如下：

- memory 可以保存更多历史事实和过程信息
- context 只保留当前 step 的最小必要集合
- 不是所有 memory 都应进入 context
- 每轮 ReAct 都应重建 context，而不是继承上轮完整工作集

### 10.7 Context 生命周期

建议明确以下生命周期：

#### 生成

- step 开始前由 `Context Harness` 构建
- 基于当前 objective 与 policy 生成第一版 `ContextBundle`

#### 刷新

- 每轮 ReAct 后可按需刷新
- 当 evidence、artifact、memory、policy 发生显著变化时触发重建

#### 归档

- step 结束后，只将通过 commit gate 的内容进入长期层
- 未验证内容保留在 ephemeral working set 或直接丢弃

#### 回滚后重建

- rollback 到 checkpoint 后，context 必须根据 checkpoint 对应的 state/memory 重建
- 不允许沿用回滚前构建的 context bundle

### 10.8 Context Sandbox

`Context Sandbox` 是 `Context Harness` 的一部分。

它负责保证：

- step 只拿当前最小上下文切片
- subagent 只拿父任务授权的上下文子集
- tool 不能直接拿全部 memory / state
- 敏感字段默认脱敏或不下发

### 10.9 建议增强后的 ContextBundle

当前 `ContextBundle` 建议包含：

- `objective`
- `state_slice`
- `evidence_slice`
- `artifact_slice`
- `memory_slice`
- `tool_options`
- `token_budget`

建议进一步增加：

- `context_version`
- `source_summary`
- `reliability_summary`
- `dropped_context_notes`

这样平台可以解释：

- 这次为什么选择这些上下文
- 哪些内容被裁掉
- 当前 context 的可靠性如何

### 10.10 Context Harness 的实现流水线

建议统一实现为：

```text
Gather
  -> Filter
  -> Rank
  -> Compress
  -> Budget
  -> Package
```

各阶段含义：

- `Gather`：收集候选来源
- `Filter`：过滤掉 stale / unverified / conflict / low-value 项
- `Rank`：按 relevance、trust、freshness、grounding 排序
- `Compress`：把长内容转成结构化摘要
- `Budget`：按 slice 配额裁剪
- `Package`：输出 `ContextBundle`

## 11. Memory Harness 设计

### 11.1 设计目标

`Memory Harness` 的目标不是“尽量多存”，而是：

- 区分短期工作记忆与长期稳定记忆
- 让记忆对执行有帮助，但不污染后续推理
- 保证不同 user / session / run 的记忆不串味
- 支持 checkpoint、rollback、replay、resume

一句话总结：

```text
慢写入，强约束，可回滚
```

### 11.2 记忆分层模型

建议把记忆体系拆成 5 层：

#### 1. Working Memory

- 单个 step 或单轮 ReAct 的临时工作集
- 存 observation、候选动作、临时摘要、未验证推断
- 当前步骤结束后清空、丢弃或下沉摘要

#### 2. Session Memory

- 同一 session 内的连续交互记忆
- 存当前会话目标、近期偏好、最近确认过的事实
- 默认不跨 session 继承

#### 3. Run Memory

- 单次 `TaskRun` 的过程记忆
- 存 step outcome、tool call 摘要、reflection、artifact lineage、gap notes
- 是恢复、审计、回放的主记忆层

#### 4. Semantic Memory

- 跨 run 复用的稳定知识
- 只接收经过验证的事实、规则、成功模式、失败模式、稳定摘要
- 写入必须严格门控

#### 5. Profile Memory

- 用户画像与租户画像
- 存语言偏好、输出偏好、工具偏好、风险偏好、权限边界、合规约束
- 属于长期配置层，而不是任务事实层

### 11.3 哪些是短期记忆，哪些是长期记忆

短期记忆包括：

- `Working Memory`
- `Session Memory`
- `Run Memory` 中未完成步骤的暂存部分

长期记忆包括：

- `Semantic Memory`
- `Profile Memory`
- 从成功 run 中提炼出的稳定摘要

原则如下：

- 越靠近执行现场，越短期、越可回滚
- 越靠近长期知识，越保守、越难写入

### 11.4 用户画像怎么设计

建议把画像分成两类：

#### 1. Explicit Profile

- 用户明确设置或明确确认
- 例如默认语言、输出格式、工具禁用偏好、风险偏好
- 优先级最高

#### 2. Inferred Preference

- 从多次行为中推断
- 必须携带置信度
- 达到阈值后才可生效

画像写入规则：

- 单次行为不自动修改画像
- 高频重复偏好才允许推断
- 高风险画像项必须显式确认
- `explicit` 与 `inferred` 必须分开存储

### 11.5 记忆污染治理

#### 风险来源

- 把 `thought` 当事实写入长期记忆
- 把失败工具输出写成有效 observation
- 把低覆盖率推断当成稳定结论
- 多轮 ReAct 摘要不断膨胀上下文
- 把 fallback 低质量结果写成正常结论
- 把回滚前状态遗留到回滚后执行路径

#### 基本原则

- chain-of-thought 不进入长期记忆
- 未验证输出不进入高优先级 context
- 低置信结论不能进入 `Semantic / Profile Memory`
- 失败分支中的中间结果默认只留在 staging
- 任何长期记忆都必须可追溯来源

### 11.6 Memory Commit Gate

建议所有稳定记忆都经过统一写入门禁：

```text
Tool/SubAgent Output
  -> Observation
  -> Validation
  -> Reflection
  -> Trust Assignment
  -> Memory Commit Gate
  -> Run / Semantic / Profile Memory
```

`Memory Commit Gate` 至少检查：

- 来源是否合法
- 是否通过 validation
- trust level 是否满足目标层要求
- 是否与已有高置信记忆冲突
- 是否只是重复摘要
- 是否带有 `degraded / stale / conflicted` 标记

### 11.7 命名空间、冲突检测与去重

#### 命名空间隔离

建议至少按以下维度隔离：

- `tenant_id`
- `org_id`
- `user_id`
- `session_id`
- `task_id`
- `task_run_id`

#### 冲突检测

新记忆写入前要判断：

- 是否与现有高置信事实冲突
- 是否来自旧版本 artifact 或旧 plan
- 是否只是语义重复

冲突时不要直接覆盖，而应：

- 标记 `conflicted`
- 建立 `supersedes / superseded_by` 关系
- 交给 review 或后续步骤补证据

#### 去重策略

- 同义 observation 折叠
- 重复 tool output 摘要聚合
- step 结束时只保留 outcome summary

### 11.8 记忆与 Context 的边界

必须明确：

```text
memory = 仓库
context = 当前工作台
```

这意味着：

- 不是所有 memory 都进入 context
- context 每轮都应重建，而不是继承完整历史
- 只有通过筛选的高价值记忆才进入 `memory_slice`
- tool 与 subagent 不能直接拿全量 memory

### 11.9 回滚、Checkpoint 与 Event Log

建议同时具备 3 个机制：

#### 1. Checkpoint

至少在以下时机创建：

- step 开始前
- 局部 ReAct 开始前
- draft / review / revise 前
- 高风险 tool 执行前

checkpoint 建议记录：

- state ref
- memory ref
- artifact ref
- plan version
- budget snapshot

#### 2. Staging Memory

- 当前 step / tool / subagent 输出先写 staging
- 通过验证后再 promote 到稳定层
- 失败或回滚时直接丢弃 staging

#### 3. Append-only Event Log

建议记录：

- `step_started`
- `tool_called`
- `observation_created`
- `memory_committed`
- `checkpoint_created`
- `rollback_applied`

这样可以基于事件重建某一时刻的运行态。

### 11.10 分层回滚策略

不同记忆层的回滚方式应不同：

- `Working Memory`：直接丢弃
- `Session Memory`：回退到最近稳定摘要
- `Run Memory`：回滚到指定 checkpoint 版本
- `Semantic Memory`：不随每次 run 自动回滚，而是做 compensation 或失效标记
- `Profile Memory`：不允许被单次失败任务随意改写

### 11.11 延迟提交到长期记忆

建议 `Semantic / Profile Memory` 默认采用延迟提交：

- run 成功
- 结果通过 review 或 evaluation
- 或用户显式确认

满足以上条件后，再从 `Run Memory` 中提炼稳定摘要写入长期层。

这样可以显著降低把短期错误状态写成长期事实的风险。

### 11.12 示例 Schema

下面给出一组贴近实现的示例 schema，用来约束 `Memory Harness` 的核心对象。

```python
class MemoryRecord(BaseModel):
    memory_id: str
    scope: Literal["working", "session", "run", "semantic", "profile"]
    namespace: dict[str, str]
    kind: Literal[
        "observation",
        "evidence",
        "analysis",
        "reflection",
        "artifact",
        "preference",
        "error",
    ]
    trust_level: Literal["unverified", "provisional", "verified", "final"]
    source: Literal["tool", "subagent", "reflection", "system", "user"]
    summary: str
    payload: dict[str, Any]
    degraded: bool = False
    stale: bool = False
    conflict_refs: list[str] = []
    created_at: datetime
    checkpoint_ref: str | None = None
    related_task_run_id: str | None = None
    related_step_id: str | None = None


class ProfileRecord(BaseModel):
    profile_id: str
    profile_type: Literal["explicit", "inferred"]
    scope: Literal["tenant", "org", "user"]
    subject_id: str
    preference_key: str
    preference_value: Any
    confidence: float | None = None
    source_count: int = 1
    confirmed_by_user: bool = False
    updated_at: datetime


class MemoryCheckpoint(BaseModel):
    checkpoint_id: str
    task_run_id: str
    step_id: str | None = None
    state_ref: str
    memory_ref: str
    artifact_ref: str | None = None
    plan_version: str | None = None
    budget_snapshot: dict[str, Any]
    created_at: datetime


class RollbackAction(BaseModel):
    rollback_id: str
    task_run_id: str
    target_checkpoint_id: str
    rollback_scope: Literal["working", "step", "run", "artifact"]
    reason: str
    created_at: datetime
```

这些对象对应前面的设计原则：

- `MemoryRecord` 负责统一承载短期与长期记忆元数据
- `ProfileRecord` 负责隔离显式画像与推断画像
- `MemoryCheckpoint` 负责恢复、重放与 resume
- `RollbackAction` 负责把回滚动作本身审计化

### 11.13 建议的最小可落地版本

MVP 阶段优先实现：

- `Working Memory`
- `Run Memory`
- `Profile Memory`
- `Memory Commit Gate`
- `Checkpoint + Rollback`
- `ToolCall / Step Event Log`

第一阶段不必急着做复杂的跨任务语义记忆检索，先把任务内记忆做对。

## 12. Prompting Layer 设计

### 12.1 为什么需要 Prompting Layer

`prompt engineering` 有必要设计，但不应成为平台主轴。

在通用 agent 平台里，prompt 的正确定位是：

- 运行时控制面的表达层
- 结构化约束到模型输入的翻译层
- `TaskSpec / StepSpec / ContextBundle / Policy` 的投影层

不应该把 prompt 当成：

- 流程编排层
- 状态管理层
- 权限系统
- 长期业务规则承载层

一句话总结：

```text
不是用 prompt 设计系统，而是让 prompt 成为系统设计的投影层
```

### 12.2 Prompting Layer 的职责

`Prompting Layer` 主要负责：

- 组装平台级 system scaffold
- 根据 `StepSpec + ContextBundle + ToolSpec + Policy` 构造当前步输入
- 对局部 ReAct、tool use、reflection、memory distillation 使用不同 prompt 模板
- 统一约束输出格式、动作边界、停止条件表达
- 做 prompt 版本管理、回归评估与灰度切换

### 12.3 与其他模块的边界

必须明确以下边界：

- `Policy` 决定什么允许，`Prompting` 负责把允许范围表达给模型
- `Context Harness` 决定给什么信息，`Prompting` 负责怎么组织这些信息
- `Memory Harness` 决定能写什么，`Prompting` 只负责生成摘要/提炼类指令
- `Execution Harness` 决定工具怎么执行，`Prompting` 只负责动作选择表达
- `Skill` 可以补充领域提示，但不能覆盖平台基础约束

因此：

- prompt 不决定规则
- prompt 表达规则

### 12.4 建议的 Prompt 分层

建议拆成以下几层：

#### 1. Platform Prompt Scaffold

- 平台级基础角色
- 平台级禁止事项
- 通用输出纪律
- 通用安全边界

#### 2. Runtime Prompt Builder

- 根据 runtime contract 组装一次模型调用输入
- 注入 `TaskSpec / StepSpec / ContextBundle / ToolSpec / PolicyProfile`

#### 3. Step Prompt Templates

- 针对不同 step pattern 使用不同模板
- 例如 evidence collection、drafting、review、memory distillation

#### 4. Tool Prompt Adapters

- 把工具候选集、工具 schema、调用约束转成模型可消费格式

#### 5. Reflection Prompt

- 只负责继续、停止、重试、fallback、replan 建议
- 不与 planner prompt 混用

#### 6. Skill Prompt Extensions

- 每个 skill 提供少量领域增强提示
- 但必须运行在平台 scaffold 之下

### 12.5 最值得单独设计的 Prompt 类型

优先建议单独设计以下 5 类：

- `Planner Prompt`
- `React Step Prompt`
- `Tool Selection Prompt`
- `Reflection Prompt`
- `Memory Distillation Prompt`

设计原则：

- 每类 prompt 目标单一
- 不混合过多职责
- 输出契约稳定
- 支持独立评测

### 12.6 Prompt 输入模型

建议统一按结构化输入生成 prompt，而不是在业务代码里直接拼接长字符串。

推荐输入：

- `TaskSpec`
- `StepSpec`
- `ContextBundle`
- `ToolSpec[]`
- `PolicyProfile`
- `PromptProfile`
- `PromptVersion`

生成流程建议为：

```text
runtime contracts
  -> prompt builder
  -> model input payload
```

### 12.7 Prompt 契约建议

建议至少补齐以下 prompt 相关契约。

#### `PromptProfile`

描述某类任务或某个用户/租户默认采用的 prompt 偏好与控制项：

- `profile_id`
- `scope`，例如 platform / tenant / user / skill
- `default_language`
- `verbosity_level`
- `reasoning_style`
- `format_preferences`
- `safety_mode`
- `enabled_prompt_packs`

#### `PromptSpec`

描述某一份 prompt 模板本身：

- `prompt_id`
- `prompt_version`
- `scope`
- `purpose`
- `target_model_family`
- `expected_output_schema`
- `template_parts`
- `guardrails`
- `change_log`

#### `PromptBuildRequest`

描述一次 prompt 构建输入：

- `task_spec_ref`
- `step_spec_ref`
- `context_bundle_ref`
- `tool_specs_ref`
- `policy_profile_ref`
- `prompt_profile_ref`
- `prompt_spec_ref`

#### `PromptBuildResult`

描述 prompt builder 的产出：

- `prompt_build_id`
- `resolved_prompt_version`
- `system_prompt`
- `developer_prompt`
- `user_prompt`
- `tool_instructions`
- `output_contract`
- `build_notes`

#### `PromptEvaluationCase`

描述一条 prompt 回归样例：

- `case_id`
- `input_fixture`
- `expected_behavior`
- `expected_tool_usage`
- `expected_output_schema`
- `assertions`

#### `PromptRegressionReport`

描述某次 prompt 版本验证结果：

- `report_id`
- `prompt_spec_ref`
- `baseline_version`
- `candidate_version`
- `pass_rate`
- `regressions`
- `tool_misuse_rate`
- `schema_violation_rate`
- `notes`

### 12.8 示例 Schema

下面给出一组贴近实现的示例 schema，用来约束 `Prompting Layer` 的核心对象。

```python
class PromptProfile(BaseModel):
    profile_id: str
    scope: Literal["platform", "tenant", "user", "skill"]
    default_language: str = "zh-CN"
    verbosity_level: Literal["low", "medium", "high"] = "medium"
    reasoning_style: Literal["compact", "structured", "deliberate"] = "structured"
    format_preferences: dict[str, Any] = {}
    safety_mode: Literal["strict", "balanced", "open"] = "balanced"
    enabled_prompt_packs: list[str] = []


class PromptSpec(BaseModel):
    prompt_id: str
    prompt_version: str
    scope: Literal["platform", "skill", "step"]
    purpose: str
    target_model_family: str
    expected_output_schema: str | None = None
    template_parts: dict[str, str]
    guardrails: list[str] = []
    change_log: list[str] = []


class PromptBuildRequest(BaseModel):
    task_spec_ref: str
    step_spec_ref: str | None = None
    context_bundle_ref: str
    tool_specs_ref: list[str] = []
    policy_profile_ref: str | None = None
    prompt_profile_ref: str | None = None
    prompt_spec_ref: str


class PromptBuildResult(BaseModel):
    prompt_build_id: str
    resolved_prompt_version: str
    system_prompt: str
    developer_prompt: str | None = None
    user_prompt: str
    tool_instructions: list[str] = []
    output_contract: dict[str, Any] | None = None
    build_notes: list[str] = []
```

这些对象对应前面的分层关系：

- `PromptProfile` 负责运行时偏好与表达风格控制
- `PromptSpec` 负责模板本身的版本治理
- `PromptBuildRequest` 负责把 runtime contracts 映射到 prompt 构建输入
- `PromptBuildResult` 负责把构建结果显式化，便于缓存、审计与回归测试

### 12.9 Prompt 治理与版本化

建议至少具备以下治理能力：

- `prompt_id`
- `prompt_version`
- `scope`，例如 platform / skill / step
- `target_model_family`
- `expected_output_schema`
- `change_log`

任何关键 prompt 调整都应具备：

- 版本号
- 生效范围
- 回滚能力
- 回归测试记录

### 12.10 Prompt 的回归评估

`Prompting Layer` 不能只靠人工感受调优，建议配套：

- 固定样例集
- 工具调用正确率
- 输出结构合规率
- retry / fallback 触发率
- hallucination / unsupported-claim 比例
- 成本与延迟变化

尤其要关注：

- prompt 变更后是否导致工具越界使用
- prompt 变更后是否破坏 stop condition
- prompt 变更后是否增加记忆污染风险

### 12.11 不建议的做法

不建议：

- 把复杂业务逻辑硬编码进超长 system prompt
- 让 prompt 持有真实权限逻辑
- 让 prompt 决定 memory commit
- 把 recovery 策略只写在 prompt 文本里
- 让 skill prompt 绕过平台级 scaffold

### 12.12 建议的最小可落地版本

MVP 阶段优先实现：

- `Platform Prompt Scaffold`
- `Runtime Prompt Builder`
- `React Step Prompt`
- `Reflection Prompt`
- `Memory Distillation Prompt`
- `prompt_version + regression cases`

第一阶段不需要追求非常复杂的 prompt marketplace 或自动 prompt 优化，先保证 prompt 层可维护、可审计、可回滚。

## 13. 沙盒设计

### 13.1 是否需要

对于通用 agent 平台，只要继续接入：

- 代码执行
- 文件写入
- 外部 API
- MCP 工具
- 浏览器操作
- Shell / Python / SQL

那么沙盒就是平台级刚需。

### 13.2 在体系中的位置

```text
Policy
  -> Guardrail
  -> Sandbox
  -> Audit
```

### 13.3 建议的沙盒分层

#### Tool Sandbox

控制：

- 可读路径
- 可写路径
- 是否允许网络
- 域名 allowlist
- 是否允许执行子进程
- CPU / 内存 / 超时 / 输出限制

#### Context Sandbox

控制：

- step 只拿当前上下文切片
- subagent 只拿父任务授权摘要
- tool 不能直接读全部 memory / state

#### Capability Sandbox

控制某类能力能做什么：

- knowledge capability 只查知识库
- filesystem capability 只读工作区
- http capability 只访问 allowlist 域名
- sql capability 只读指定 schema

#### Run Sandbox

控制某次任务的隔离边界：

- 独立工作目录
- 独立 tmp 空间
- 独立 budget / quota
- 独立 memory namespace

### 13.4 最小落地版本

短期 `Sandbox Harness MVP` 至少包含：

- 路径白名单
- 网络白名单
- 只读 / 可写目录隔离
- 高风险工具子进程执行
- step / subagent 的上下文隔离
- staging write 再 commit 的数据写入沙盒

## 14. 代码改造优先级

### 14.1 P0：先改运行时骨架

优先涉及：

- `app/container.py`
- `app/services/task_service.py`
- `app/agents/runtime.py`
- `app/agents/tools/base.py`
- `app/agents/tools/registry.py`
- `app/agents/tools/knowledge_tools.py`
- `app/rag/*`
- `app/harness/context.py`
- `app/harness/execution.py`
- `app/harness/policy.py`
- `app/harness/guardrails.py`
- `app/models/task.py`
- `app/models/artifact.py`

目标：

- 固定 `RAG -> Knowledge Capability` 边界
- 稳定工具契约
- 建立 `TaskSpec / TaskRun / StepSpec`
- 拉起 `Memory / Reflection / Recovery`

当前状态：

- 前三项已基本完成，并且已经进主链
- `RunBudget` 与 `grounded_answer` 相关契约已补入现有模型和工具注册
- 已补齐 `ReflectionHarness / RecoveryManager`，并把 query 侧恢复与反思决策收口到平台层入口
- `ContextBundle` 已进入 query runtime 与 checkpoint snapshot
- `retrieve_evidence` 已落第一版 step 级局部 `ReAct`
- `Tool Sandbox` 基础能力已落地，并支持高风险工具元数据与约束

### 14.2 P1：再统一入口与长任务能力

优先涉及：

- `app/services/query_service.py`
- `app/workflows/query_orchestrator.py`
- `app/workflows/tasks/task_orchestrator.py`
- `app/workflows/tasks/document_analysis_graph.py`
- `app/workflows/tasks/document_analysis_nodes.py`
- `app/agents/memory.py`
- `app/services/state.py`
- `app/services/sqlite_store.py`
- `app/api/v1/endpoints/query.py`
- `app/api/v1/endpoints/tasks.py`

目标：

- `query / chat / task` 收口到统一运行时
- 现有专用 task workflow 下沉为 skill
- 建立 checkpoint / rollback / replay

当前状态：

- `query / chat` 已能映射到统一 `TaskSpec`
- `query` workflow state 与 trace 已开始携带统一任务定义
- 目前还停留在“统一任务定义 + 局部 runtime 收口”阶段，尚未真正让 `query / chat / task` 共用同一套 step orchestration
- checkpoint / rollback / replay 仍未落地

### 14.3 P2：最后深化隔离、调度与扩展

优先涉及：

- `app/harness/sandbox.py`
- `app/harness/prompting.py`
- `app/agents/tools/llm_prompting.py`
- `app/services/task_dispatcher.py`
- `app/task_worker.py`
- `app/harness/evaluation.py`
- `app/services/eval_service.py`

目标：

- 强化沙盒
- 引入模型路由和成本调度
- 扩展通用 capability 与通用 tools
- 按需增加新的受控子代理

## 15. 平台边界定义

### 15.1 Kernel

`Kernel` 是平台最核心的运行时内核，负责：

- `TaskSpec / TaskRun / StepSpec` 生命周期
- `Harness Runtime`
- `Agent Runtime`
- `Bounded Local ReAct`
- `Execution / Reflection / Recovery`
- `Checkpoint / Replay / Resume`

`Kernel` 不负责：

- 领域知识实现
- 特定 skill 的业务逻辑
- 具体外部工具适配细节

### 15.2 Capability

`Capability` 是平台能力面，负责暴露稳定的能力接口。

典型能力族：

- `knowledge`
- `filesystem`
- `http`
- `browser`
- `sql`
- `code`
- `artifact`

`Capability` 负责：

- 能力契约
- 运行时适配
- 能力级沙盒与权限
- 工具或 provider 的具体实现聚合

### 15.3 Skill

`Skill` 是面向场景的任务模式封装。

`Skill` 负责：

- 输入 schema
- 任务模板
- step pattern
- 默认候选工具
- 输出 schema
- fallback 规则

`Skill` 不负责：

- 全局治理
- 权限扩张
- 直接依赖底层 `Rag*` 等具体实现

### 15.4 Interface

`Interface` 是平台入口层，负责：

- API
- session
- worker
- CLI / IDE / external trigger adapters

它只做接入与适配，不应承载领域逻辑。

### 15.5 Infra

`Infra` 负责：

- model provider
- queue / scheduler
- storage / persistence
- observability
- deployment topology

它是运行底座，不决定顶层产品抽象。

## 16. 依赖方向与架构约束

### 16.1 允许的依赖方向

推荐依赖方向：

```text
Interface -> Kernel
Kernel -> Capability Contracts
Skill -> Kernel + Capability Contracts
Capability -> Infra
Kernel -> Infra
```

### 16.2 明确禁止的依赖

必须禁止：

- `Skill -> Rag* / VectorStore / Retrieval` 具体实现
- `Tool -> Long-term Memory` 直接写入
- `SubAgent -> Full State / Full Memory` 直接访问
- `Interface -> Skill internals` 直接耦合
- `Capability -> Task/Run lifecycle` 反向控制
- `Prompt helper` 私自承载权限和策略逻辑

### 16.3 关键约束

#### 约束 1. Skill 只能依赖 capability 抽象

skill 只能看到：

- `CapabilityProvider`
- `ToolSpec`
- `ContextBundle`
- `TaskSpec / TaskRun`

#### 约束 2. Tool 只能通过 Harness 执行

tool 不允许：

- 直接修改 run state
- 直接提交 final artifact
- 直接写入长期 memory

#### 约束 3. SubAgent 必须走受控 handoff

subagent 必须具备：

- 工具白名单
- 上下文子集
- 步数限制
- 预算限制
- sandbox profile

#### 约束 4. Recovery 必须由 Harness 决定

错误恢复、fallback、rollback 不允许由 tool 或 subagent 自行决定。

## 17. 核心契约总表

### 17.1 任务与运行契约

#### `TaskSpec`

描述一次任务目标与约束，最小字段建议：

- `task_type`
- `objective`
- `input`
- `constraints`
- `budget`
- `policy_profile`
- `skill_hint`

#### `TaskRun`

描述一次具体执行实例，建议字段：

- `task_run_id`
- `task_id`
- `status`
- `current_step`
- `attempt_count`
- `budget_snapshot`
- `checkpoint_ref`
- `created_at / updated_at`

#### `StepSpec`

描述单个步骤的执行契约：

- `step_id`
- `objective`
- `allowed_tools`
- `max_turns`
- `success_criteria`
- `stop_conditions`
- `fallback_action`
- `output_schema`

### 17.2 上下文与推理契约

#### `ContextBundle`

- `objective`
- `state_slice`
- `evidence_slice`
- `artifact_slice`
- `memory_slice`
- `tool_options`
- `token_budget`

#### `ReActState`

- `step_id`
- `turn_index`
- `action_history`
- `observation_history`
- `remaining_budget`
- `exit_reason`

#### `ReactDecision`

- `decision_type`
- `selected_action`
- `selected_tool`
- `reason_summary`
- `expected_outcome`

#### `ReflectionDecision`

- `decision`
- `reason`
- `progress_delta`
- `should_continue`
- `fallback_action`

### 17.3 Prompt 契约

#### `PromptProfile`

- `profile_id`
- `scope`
- `default_language`
- `verbosity_level`
- `reasoning_style`
- `format_preferences`
- `safety_mode`

#### `PromptSpec`

- `prompt_id`
- `prompt_version`
- `scope`
- `purpose`
- `target_model_family`
- `expected_output_schema`
- `template_parts`

#### `PromptBuildRequest`

- `task_spec_ref`
- `step_spec_ref`
- `context_bundle_ref`
- `tool_specs_ref`
- `policy_profile_ref`
- `prompt_profile_ref`

#### `PromptBuildResult`

- `prompt_build_id`
- `resolved_prompt_version`
- `system_prompt`
- `developer_prompt`
- `user_prompt`
- `tool_instructions`
- `output_contract`

#### `PromptEvaluationCase`

- `case_id`
- `input_fixture`
- `expected_behavior`
- `expected_tool_usage`
- `expected_output_schema`
- `assertions`

#### `PromptRegressionReport`

- `report_id`
- `prompt_spec_ref`
- `baseline_version`
- `candidate_version`
- `pass_rate`
- `regressions`

### 17.4 工具与能力契约

#### `ToolSpec`

- `name`
- `input_schema`
- `output_schema`
- `error_model`
- `timeout_policy`
- `retry_policy`
- `side_effect_level`
- `sandbox_profile`

#### `CapabilityProvider`

- `capability_name`
- `operations`
- `required_permissions`
- `sandbox_policy`

### 17.5 状态与恢复契约

#### `Checkpoint`

- `checkpoint_id`
- `task_run_id`
- `step_id`
- `state_ref`
- `memory_ref`
- `created_at`

#### `RunEvent`

- `event_id`
- `task_run_id`
- `step_id`
- `event_type`
- `payload`
- `created_at`

#### `ToolCallRecord`

- `tool_call_id`
- `tool_name`
- `status`
- `retry_count`
- `duration_ms`
- `input_preview`
- `output_summary`
- `error`

## 18. 生命周期与状态机

### 18.1 Task 生命周期

```text
created
  -> accepted
  -> rejected
```

### 18.2 TaskRun 生命周期

```text
queued
  -> running
  -> completed
  -> failed
  -> partial_success
  -> cancelled
  -> paused
  -> resumable
```

说明：

- `partial_success` 用于带缺口但可交付的任务
- `paused` 用于人为或系统挂起
- `resumable` 用于存在有效 checkpoint 且允许恢复

### 18.3 Step 生命周期

```text
pending
  -> ready
  -> running
  -> completed
  -> failed
  -> skipped
  -> fallback_completed
```

### 18.4 ToolCall 生命周期

```text
prepared
  -> running
  -> succeeded
  -> failed
  -> timed_out
  -> fallback_applied
```

### 18.5 SubAgentRun 生命周期

```text
created
  -> running
  -> completed
  -> fallback_completed
  -> failed
  -> aborted
```

### 18.6 生命周期约束

- 只有 `completed / partial_success / failed / cancelled` 才能结束一个 run
- 只有存在有效 `Checkpoint` 才能进入 `resumable`
- `fallback_completed` 必须带缺口说明
- `failed` 与 `partial_success` 都必须附带原因分类

## 19. 兼容迁移策略

### 19.1 迁移原则

- 先保兼容，再做替换
- 先加适配层，再删旧主链
- 先统一 contract，再统一实现

### 19.2 Query 兼容

迁移期内保留：

- `query`
- `chat`
- `query_stream`
- `chat_stream`

但内部逐步改成：

```text
QueryRequest / ChatRequest
  -> Query Adapter
  -> TaskSpec
  -> Unified Runtime
```

### 19.3 Task 兼容

现有 `document_analysis` 相关 API 在迁移期继续保留，但语义上降级为：

- 首个默认 skill
- 平台能力演示用例
- 兼容旧客户端的业务入口

### 19.4 数据兼容

迁移期建议并存：

- 旧 `TaskDetail / TaskResult`
- 新 `TaskSpec / TaskRun / RunEvent / Checkpoint`

通过 adapter 做映射，避免一次性替换全部模型。

当前状态补充：

- `TaskDetail -> TaskSpec / TaskRun` 已有实际映射
- `QueryRequest / ChatRequest -> TaskSpec` 已有实际 adapter
- query graph 已引入 `dispatch_query_step`
- query graph 控制流已继续收紧为 `dispatch_query_step + orchestration_next_route`
- query runtime 已补齐 `TaskRun.current_step_id / completed_step_ids / step_attempts / step_runtimes`
- query reflection 已收口为 `ReflectionDecision`
- SSE 已补充 `step_started / step_completed / step_failed`，并继续兼容原有 query 事件
- query 已在 `check_guardrails / retrieve_evidence / self_reflect` 后创建最小 checkpoint，并支持 replay
- query `TaskRun / RunEvent / Checkpoint` 已进入 `InMemoryState / SQLiteStateStore`，且提供 `list / detail / replay / resume / recover / analytics` 读取入口
- 已补充 step/orchestration 节点写入契约校验，避免把 step 运行语义放回 glue node
- query 这条线当前剩下的是更长期的 audit 保留策略，以及面向多 workflow 的统一恢复管理器

### 19.5 删除旧链路的条件

只有当以下条件都成立时，才允许删除旧主链：

- `query / chat / task` 已全部走统一运行时
- 旧模型只剩兼容层用途
- replay / resume / audit 已切到新模型
- 关键场景回归通过

## 20. 分阶段完成定义

### 20.1 P0 完成定义

以下条件全部满足才算 P0 完成：

- `RAG` 已通过 capability 抽象暴露，不再作为顶层主对象
- 已存在 `TaskSpec / TaskRun / StepSpec`
- `ToolSpec`、`ContextBundle`、`Checkpoint` 等核心契约已落地
- `Memory Harness / Reflection Harness / Recovery Manager` 已接入主流程
- 至少一个步骤完成局部 `ReAct` 化

当前判断：

- 截至 2026-06-16，`P0` 已完成
- 已完成项包括 `Knowledge Capability` 边界、`TaskSpec / TaskRun / StepSpec` 契约、`ContextBundle / Checkpoint` 主链、平台化 `Reflection / Recovery`、以及 `retrieve_evidence` 的 step 级局部 `ReAct`
- 后续重点已转向 `P1` 的统一 step runtime、skill 化和 artifact-first 主链

### 20.2 P1 完成定义

以下条件全部满足才算 P1 完成：

- `query / chat / task` 都能映射到统一 runtime
- 现有专用 task flow 已下沉为 skill
- checkpoint / rollback / replay / resume 可用
- `TaskRun` 与 `RunEvent` 可查询
- artifact-first 输出成为主链

当前判断：

- `P1` 还处于起步阶段
- 已经完成的是“`query / chat` 能映射到统一 runtime 对象”
- 尚未完成的是“统一 step runtime、统一回放恢复、task flow 下沉为 skill、artifact-first 主链”

### 20.3 P2 完成定义

以下条件全部满足才算 P2 完成：

- 高风险 tool 已接入沙盒
- `Knowledge Capability` 可独立演进甚至独立部署
- 模型路由和成本调度可用
- 新 capability / skill 的接入不需要改动 runtime 骨架
- 受控子代理具备稳定 handoff 与审计记录

## 21. 建议的实施路线

### 第一批必须完成

- 明确 `RAG -> Knowledge Capability` 边界
- 引入 `TaskSpec / TaskRun / StepSpec`
- 新增 `Memory Harness / Reflection Harness / Recovery Manager`
- 在 `retrieve_evidence` 步骤先落第一版局部 ReAct
- 为高风险工具加入 `Tool Sandbox` 基础能力

实施备注：

- 前两项已完成，并已进入现有 query/task 主链
- 第三到第五项已完成并进入现有主链；当前最优先事项已切换到 `P1`

### 第二批建议完成

- 将现有专用任务流下沉为 skill
- 统一 `query / chat / task` 到同一任务运行时
- 加入 checkpoint / rollback / replay
- 扩展 artifact-first 交付物

实施备注：

- 这一批已经开始做第一步，即 `query / chat -> TaskSpec` 的统一投影
- 但距离“统一到同一任务运行时”还差 runtime 编排层和状态层的彻底收口

### 第三批再考虑

- 高风险能力的强隔离执行
- knowledge capability 的服务化
- 更多通用 capability / tools
- 新的受控子代理
- 模型路由、成本调度、预取优化

## 22. 成功标准

如果这套方案落地，系统应具备：

- 新增任务时，主要是配置 `TaskSpec + Tool Set + Step Pattern`
- `RAG` 可以作为知识能力被复用，而不是继续主导顶层架构
- agent 每一步都有明确预算、边界、记录、checkpoint 与退出条件
- 任务结果可追溯、可比较、可回放、可回滚
- 稳定性问题主要由 `Harness` 兜底，而不是散落在 workflow 分支和 prompt 中

## 23. 结论

本项目下一阶段不应继续定义为“RAG 项目上的专用 agent”，而应定义为：

`General-Purpose Harnessed Agent Runtime`

一句话总结：

> 核心不是继续强化 RAG 技巧，而是完成一次以 `Harness` 为中心的运行时重构，让 `RAG` 成为能力，让 `ReAct` 受约束，让 `Memory / Recovery / Sandbox` 成为平台级底座。

## 24. 统一代码骨架草案

这一节给出一份贴近实现的统一模型草案，目标不是直接替代全部现有模型，而是作为新 runtime 第一批核心对象的参考起点。

### 24.1 推荐文件布局

```text
app/
  kernel/
    contracts/
      task.py
      step.py
      context.py
      memory.py
      prompting.py
      execution.py
  capabilities/
    knowledge/
      contracts.py
```

### 24.2 核心模型草案

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BudgetSpec(BaseModel):
    max_steps: int = 12
    max_tool_calls: int = 20
    max_subagent_calls: int = 2
    max_input_tokens: int = 24_000
    max_output_tokens: int = 8_000
    max_runtime_seconds: int = 300


class PolicyProfileRef(BaseModel):
    profile_id: str
    version: str | None = None


class TaskSpec(BaseModel):
    task_id: str
    task_type: str
    objective: str
    input: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    policy_profile: PolicyProfileRef | None = None
    skill_hint: str | None = None
    created_at: datetime


class TaskRun(BaseModel):
    task_run_id: str
    task_id: str
    status: Literal[
        "queued",
        "running",
        "completed",
        "failed",
        "partial_success",
        "cancelled",
        "paused",
        "resumable",
    ]
    current_step: str | None = None
    attempt_count: int = 0
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    checkpoint_ref: str | None = None
    created_at: datetime
    updated_at: datetime


class StepSpec(BaseModel):
    step_id: str
    objective: str
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int = 3
    success_criteria: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    fallback_action: Literal["degrade", "skip_with_gap", "abort"] = "skip_with_gap"
    output_schema: str | None = None


class ContextBundle(BaseModel):
    context_version: int = 1
    objective: str
    state_slice: dict[str, Any] = Field(default_factory=dict)
    evidence_slice: list[dict[str, Any]] = Field(default_factory=list)
    artifact_slice: list[dict[str, Any]] = Field(default_factory=list)
    memory_slice: list[dict[str, Any]] = Field(default_factory=list)
    tool_options: list[str] = Field(default_factory=list)
    token_budget: int = 8000
    source_summary: dict[str, Any] = Field(default_factory=dict)
    reliability_summary: dict[str, Any] = Field(default_factory=dict)
    dropped_context_notes: list[str] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    memory_id: str
    scope: Literal["working", "session", "run", "semantic", "profile"]
    namespace: dict[str, str] = Field(default_factory=dict)
    kind: Literal[
        "observation",
        "evidence",
        "analysis",
        "reflection",
        "artifact",
        "preference",
        "error",
    ]
    trust_level: Literal["unverified", "provisional", "verified", "final"]
    source: Literal["tool", "subagent", "reflection", "system", "user"]
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    stale: bool = False
    conflict_refs: list[str] = Field(default_factory=list)
    checkpoint_ref: str | None = None
    related_task_run_id: str | None = None
    related_step_id: str | None = None
    created_at: datetime


class PromptSpec(BaseModel):
    prompt_id: str
    prompt_version: str
    scope: Literal["platform", "skill", "step"]
    purpose: str
    target_model_family: str
    expected_output_schema: str | None = None
    template_parts: dict[str, str] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)
    change_log: list[str] = Field(default_factory=list)


class PromptBuildRequest(BaseModel):
    task_spec_ref: str
    step_spec_ref: str | None = None
    context_bundle_ref: str
    tool_specs_ref: list[str] = Field(default_factory=list)
    policy_profile_ref: str | None = None
    prompt_profile_ref: str | None = None
    prompt_spec_ref: str


class PromptBuildResult(BaseModel):
    prompt_build_id: str
    resolved_prompt_version: str
    system_prompt: str
    developer_prompt: str | None = None
    user_prompt: str
    tool_instructions: list[str] = Field(default_factory=list)
    output_contract: dict[str, Any] | None = None
    build_notes: list[str] = Field(default_factory=list)


class EvidencePack(BaseModel):
    query: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    coverage_score: float | None = None
    relevance_score: float | None = None


class RetrievalQualityReport(BaseModel):
    query: str
    overall_score: float
    coverage_score: float
    relevance_score: float
    confidence_score: float
    suggested_actions: list[str] = Field(default_factory=list)


class GroundedContext(BaseModel):
    objective: str
    evidence_pack_ref: str
    grounded_facts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_gaps: list[str] = Field(default_factory=list)


class GraphSubgraph(BaseModel):
    root_entities: list[str] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class Checkpoint(BaseModel):
    checkpoint_id: str
    task_run_id: str
    step_id: str | None = None
    state_ref: str
    memory_ref: str
    created_at: datetime
```

### 24.3 这一版草案的使用原则

- 第一批实现只保留高价值字段，不追求一次把所有细节补满
- 新模型优先服务统一 runtime，不急着立刻替换全部旧业务模型
- 旧 `TaskDetail / TaskResult / QueryRequest` 应通过 adapter 映射到这些新对象
- 字段能引用就尽量引用，避免在 run state 里复制大块对象
- `Knowledge Capability` 的增强策略对象建议放在 `capabilities/knowledge/contracts.py`，不要继续塞回平台核心模型

### 24.4 建议的第一步落地顺序

1. 先建 `TaskSpec / TaskRun / StepSpec`（已完成）
2. 再建 `ContextBundle / MemoryRecord / Checkpoint`（`ContextBundle / Checkpoint` 已完成，`MemoryRecord` 仍未独立建模）
3. 再建 `PromptSpec / PromptBuildRequest / PromptBuildResult`（未完成）
4. 再建 `EvidencePack / RetrievalQualityReport / GroundedContext / GraphSubgraph`（部分完成，`EvidencePack / RetrievalQualityReport` 已进入知识能力链路）
5. 最后用 adapter 把旧 query/task 流程接到新模型上（部分完成，`task` 与 `query/chat` 已有 adapter，但 runtime 仍未完全统一）

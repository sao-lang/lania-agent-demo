# Agent 能力管理设计方案

> 文档角色：核心管理文档
>  
> 是否建议默认加载：是
>  
> 适合回答的问题：
> - `session / memory / skill / tool / command / permission / sub-agent / hook / context / tech-stack` 各归谁管理
> - 这些能力该由哪一层持有
> - 当前项目在这些方面做到什么程度
>  
> 建议搭配阅读：
> - `harness-capability-integration.md`
> - `harness-runtime-contracts.md`

## 1. 文档目的

本文承接以下重构文档：

- [harness-composition-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-refactor-plan.md)
- [harness-runtime-contracts.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-runtime-contracts.md)
- [harness-composition-migration-checklist.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-migration-checklist.md)
- [harness-final-cohesion-shape.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-final-cohesion-shape.md)

本文只回答一个问题：

在当前项目里，如果要把 `session / memory / skills / tools / command / permission / sub-agent / hook / tech-stack / context` 这些 agent 能力统一管理，应如何分层、归口、注册、执行与演进。

重点不是列名词清单，而是给出“谁来管理什么”的清晰方案。

## 2. 设计目标

这份能力管理方案的目标如下：

1. 每类能力只有一个主管理层，不再多头管理。
2. 扩展通过注册和继承完成，不通过到处注入特判完成。
3. 执行通过组合完成，不让某个大类承担过多职责。
4. `query / chat / task` 可以共用同一套能力管理框架。
5. `RAG` 等领域能力继续保留域内实现，但只能通过统一能力面进入 runtime。

## 3. 总体分层

能力管理统一分为五层：

```text
Entry Layer
  -> Session / Task / Request Adapter

State Layer
  -> Context / Memory / Run State / Artifact Lineage

Workflow Layer
  -> Skill / Recipe / Stage

Capability Layer
  -> Tool / Command / SubAgent / Facade Adapter

Governance Layer
  -> Permission / Policy / Guardrail / Budget / Audit / Hook

Infra Layer
  -> Provider / Container / Storage / Model / Sandbox / Trace
```

这五层是“管理归口”，不是执行顺序。

## 4. 主管理对象与职责归口

### 4.1 Session 由 Entry + State 联合管理

#### 定位

`Session` 代表跨轮交互容器，不代表一次具体执行任务。

#### 责任边界

由 `Entry Layer` 负责：

- 建立 session 标识
- 关联 query/chat 请求
- 暴露 session 查询与摘要接口

由 `State Layer` 负责：

- session message 持久化
- summary 压缩
- session 与 task/run 的关联映射

#### 不应承担

- 具体工具执行
- 领域能力调用
- 权限判定

#### 对当前项目的落点

现有 session 主要在 query 侧，后续应保留：

- `SessionService` 或 `QueryService` 暂时作为 facade
- session 数据继续进入 state/persistence

但应避免把 session 逻辑留在 query engine 的内部 helper 中继续扩张。

### 4.2 Memory 统一由 State Layer 管理

#### 定位

`Memory` 是跨 step / 跨 run / 跨 session 的状态沉淀，不等于当前上下文。

#### 建议拆分

统一内存面分为：

- `Run Memory`：当前执行态摘要
- `Task Memory`：任务推进过程、plan revision、tool call、failure
- `Session Memory`：多轮消息和 summary
- `Artifact Memory`：产物版本与 lineage
- `Reflection Memory`：反思决策与恢复信息
- `SubAgent Memory`：handoff 和子代理执行摘要

#### 管理方式

统一由 `MemoryManager` 或现有 `TaskMemory` 演进版负责：

- 创建
- 读取
- 持久化
- 转为 runtime contract
- 供 trace / replay / analytics 消费

#### 对当前项目的落点

现有 `TaskMemory` 已经是一个较好的起点，但后续建议：

- 把 query memory 也统一映射到同一 memory data plane
- 不再让 query 侧自己隐式保留另一套会话内存模型

### 4.3 Skill 统一由 Workflow Layer 管理

#### 定位

`Skill` 代表某类任务的“执行模板”，不是原子能力。

#### 职责

- 声明 `task_type`
- 构造初始 state
- 选择使用哪个 recipe
- 决定默认 stage 组合和输出契约

#### 不应承担

- 直接执行 tool
- 直接访问 domain service
- 直接写权限或 fallback 逻辑

#### 管理方式

统一通过 `SkillRegistry` 管理：

```python
class SkillRegistry(Protocol):
    def register(self, skill: TaskSkill) -> None: ...
    def get(self, task_type: str) -> TaskSkill: ...
```

#### 对当前项目的落点

当前 task 侧已有 `TaskSkillRegistry`，建议继续推进：

- query/chat 也逐步 skill 化
- skill 只保留“任务模板”职责

### 4.4 Tool 统一由 Capability Layer 管理

#### 定位

`Tool` 是 runtime 可以稳定调用的唯一能力入口。

#### 职责

- 暴露 schema
- 接收结构化输入
- 返回结构化输出
- 调用 facade 或下游 adapter

#### 管理方式

统一通过 `ToolRegistry + ToolExecutor` 管理：

- `ToolRegistry` 管注册、schema、查找
- `ToolExecutor` 管 timeout、retry、sandbox、policy、audit

#### 对当前项目的落点

这一块保持现有方向即可，但要继续收边界：

- workflow/stage 只能调 tool
- 不再直接调 capability/service

### 4.5 Command 作为 Tool 子类管理

#### 定位

`Command` 不是独立顶层能力，而是 tool 的一种特化形式。

#### 原因

如果把 command 升成单独并列体系，很容易出现：

- command registry
- tool registry
- 额外权限模型
- 额外审计链

这会平白长出第二套执行系统。

#### 推荐方案

把 command 收成：

- `CommandTool`
- `RepositoryCommandTool`
- `ShellCommandTool`

统一仍进入 `ToolRegistry + ToolExecutor`

#### 对当前项目的落点

当前项目还没有独立 command runtime，这反而是好事。后续如果要加 shell/repo 操作，建议直接做成 tool。

### 4.6 Permission / Policy / Guardrail 统一由 Governance Layer 管理

#### 定位

这是 agent 系统的治理面，不属于 workflow，不属于 tool，也不属于 domain。

#### 统一拆分

- `Permission`：调用方和资源边界
- `Policy`：当前任务允许做什么
- `Guardrail`：当前输入/动作是否安全
- `Budget`：步数、成本、token、tool call 限额

#### 管理方式

所有治理能力都不直接散落在 stage 中，而统一由：

- `GuardrailEngine`
- `PolicyEngine`
- `BudgetController`
- `SandboxEngine`

来管理。

#### 对当前项目的落点

当前已经有：

- `GuardrailEngine`
- `PolicyEngine`
- query 侧 permission filter
- `ToolSandbox`

后续要做的是：

- 统一 query/task 的权限表达
- 让治理逻辑从 nodes/stages 中进一步抽离

### 4.7 Sub-Agent 统一由 Capability Layer 管理

#### 定位

`Sub-Agent` 是受控执行资源，不是顶层 runtime。

#### 职责

- 暴露固定 action schema
- 只允许调用白名单工具
- 为主 agent 提供补充能力

#### 管理方式

通过 `SubAgentRegistry + SubAgentRuntime` 管理：

- registry 管注册和 schema
- runtime 管 handoff、执行、审计

#### 不应承担

- 自己拥有独立 policy engine
- 自己决定主任务推进
- 直接对外暴露入口

#### 对当前项目的落点

当前 `ControlledSubAgent`、`SubAgentRegistry`、`SubAgentRuntime` 的方向是对的，应保持。

### 4.8 Hook 统一由 Governance Layer 管理

#### 定位

`Hook` 不是业务对象，而是运行时扩展点。

#### 推荐 hook 面

建议最终只暴露有限 hook：

- `before_stage`
- `after_stage`
- `before_tool`
- `after_tool`
- `on_failure`
- `on_checkpoint`
- `on_run_completed`

#### 管理方式

统一通过 `HookRegistry` 或 runtime event bus 管理：

```python
class HookRegistry(Protocol):
    def register(self, hook: RuntimeHook) -> None: ...
    def emit(self, event_name: str, payload: dict) -> None: ...
```

#### 对当前项目的落点

当前更多是：

- `trace.record(...)`
- checkpoint event
- replay/resume recover 事件

这说明已经有事件流，但还没有正式 hook 机制。后续可以基于现有 trace/checkpoint 事件收敛成 hook 面。

### 4.9 Context 统一由 State Layer 管理

#### 定位

`Context` 是当前 step 可消费的最小状态切片，不等于 memory。

#### 管理目标

必须满足：

- 结构化
- 可切片
- 可预算
- 可 checkpoint
- 可 replay

#### 推荐拆分

- `State Slice`
- `Evidence Slice`
- `Artifact Slice`
- `Memory Slice`
- `Tool Options`
- `Budget Slice`

#### 对当前项目的落点

现有 `ContextBundle` 是正确方向，后续应继续：

- 让 query/task 都统一进入 `ContextBundle`
- 把 query/task 专用上下文逻辑从 harness 通用层移走

### 4.10 Tech Stack 统一由 Infra Layer 管理

#### 定位

`Tech Stack` 不是 agent 能力，而是能力的承载方式。

#### 包含内容

- LLM provider
- vector store
- sqlite / remote db
- sandbox worker
- model router
- persistence
- provider factory
- container injection

#### 管理方式

统一由：

- `Settings`
- `Factory`
- `Provider Registry`
- `Container`

管理。

#### 不应渗透到

- stage
- skill
- tool 使用方

## 5. 推荐的统一管理接口

### 5.1 State 管理面

```python
class StateManager(Protocol):
    def load_session(self, session_id: str) -> Any: ...
    def save_session(self, session_id: str, payload: Any) -> None: ...
    def create_task_run(self, spec: TaskSpec) -> TaskRun: ...
    def persist_checkpoint(self, run_id: str, checkpoint: CheckpointRecord) -> None: ...
    def record_memory(self, run_id: str, record: MemoryRecord) -> None: ...
```

### 5.2 Workflow 管理面

```python
class SkillRegistry(Protocol):
    def register(self, skill: TaskSkill) -> None: ...
    def get(self, task_type: str) -> TaskSkill: ...
```

### 5.3 Capability 管理面

```python
class ToolRegistry(Protocol):
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
```

```python
class SubAgentRegistry(Protocol):
    def register(self, agent: RegisteredSubAgent) -> None: ...
    def get(self, name: str) -> RegisteredSubAgent: ...
```

### 5.4 Governance 管理面

```python
class GovernanceManager(Protocol):
    def check_guardrail(self, scope: str, payload: dict) -> GuardrailDecision: ...
    def check_policy(self, scope: str, payload: dict) -> PolicyDecision: ...
    def resolve_budget(self, run_id: str) -> Any: ...
    def emit_hook(self, event_name: str, payload: dict) -> None: ...
```

## 6. 推荐目录收口

建议最终按“谁来管理什么”来收目录，而不是按历史来源收目录：

```text
app/
  entry/
    adapters/
    session_service.py
    task_entry.py
  state/
    memory_manager.py
    session_store.py
    task_store.py
    context_provider.py
  workflow/
    skills/
    recipes/
    stages/
  capability/
    tools/
    subagents/
    adapters/
  governance/
    guardrails/
    policy/
    budget/
    hooks/
    audit/
  domain/
    rag/
    repository/
    database/
    artifact/
  infra/
    providers/
    container/
    persistence/
    sandbox/
    routing/
```

如果暂时不大改目录，也应先按这个归属关系重构依赖方向。

## 7. 与当前项目的成熟度对照

### 7.1 已经相对成熟

- `ToolRegistry + ExecutionHarness + ToolSandbox`
- `PolicyEngine / GuardrailEngine`
- `SubAgentRegistry / SubAgentRuntime`
- `TaskSpec / TaskRun / Checkpoint / Replay / Resume`
- `TaskMemory` 主链

### 7.2 已有基础但仍双轨

- `session`
- `memory`
- `context`
- `query / task` 运行语义
- `knowledge / rag` 入口

### 7.3 仍明显不足

- 正式 `HookRegistry`
- command 作为 tool 特化的统一定义
- query/chat 全面 skill 化
- query/task 权限治理统一表达

## 8. 分阶段落地建议

### Phase 1：先把管理归口立住

优先做：

1. 明确 `session / memory / skill / tool / sub-agent / governance / context` 各自归谁管
2. 禁止新增跨层直连
3. 新能力统一先接 registry

### Phase 2：统一知识能力接入

优先做：

1. `RagFacade`
2. `rag_* tools`
3. workflow 统一调 tool

### Phase 3：把双轨 state 收起来

优先做：

1. query memory 和 task memory 统一到同一 data plane
2. query context 和 task context 统一到 `ContextBundle`
3. session 与 task/run 建立稳定映射

### Phase 4：补 hook 与 command 特化

优先做：

1. `HookRegistry`
2. runtime event bus
3. `CommandTool` 族

## 9. 验收标准

完成后应至少满足以下标准：

1. 每类能力都能回答“谁是主管理层”。
2. 新增一个 tool/sub-agent/skill 时，只需要在各自 registry 注册。
3. query/task 不再分别维护两套平行 memory/context 语义。
4. governance 不再散落在 stage/node 内部。
5. `RAG` 继续保留域内实现，但上层只能通过 tool/facade 访问。

## 10. 一句话结论

这套设计的核心不是把 agent 相关名词列全，而是把它们分配给唯一管理层：

- `session / memory / context` 归 `state`
- `skill / recipe / stage` 归 `workflow`
- `tool / command / sub-agent` 归 `capability`
- `permission / policy / hook / audit` 归 `governance`
- `tech-stack` 归 `infra`

只有这样，能力才会持续增加，架构不会继续发散。

# Harness Runtime 接口契约草案

> 文档角色：核心契约文档
>  
> 是否建议默认加载：是
>  
> 适合回答的问题：
> - 哪些接口属于稳定面
> - 哪些对象允许继承扩展
> - 哪些类只是兼容层
>  
> 建议搭配阅读：
> - `harness-composition-refactor-plan.md`
> - `harness-composition-migration-checklist.md`

## 1. 文档目的

本文是 [harness-composition-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-refactor-plan.md) 的配套契约文档，用来明确：

- 哪些接口属于平台稳定面
- 哪些对象允许靠继承扩展
- 哪些执行行为必须通过组合完成
- 哪些类只属于迁移期兼容层

本文的目标是减少“边拆边重新发明接口”的情况。

## 2. 契约分层

本文将接口分为三类：

### 2.1 平台稳定接口

这些接口一旦落地，应尽量保持稳定：

- `RuntimeEntry`
- `HarnessKernel`
- `HarnessRecipe`
- `HarnessStage`
- `RuntimeContext`
- `Tool`
- `ToolRegistry`
- `ToolExecutor`
- `SubAgentRuntime`
- `RagFacade`

### 2.2 领域实现接口

这些接口可以按领域扩展，但不应反向污染平台层：

- `QueryRecipe`
- `TaskRecipe`
- `QueryContextProvider`
- `TaskContextProvider`
- `EvidenceSubAgent`
- `ReviewSubAgent`
- `RagToolAdapter`

### 2.3 迁移期兼容层

以下对象在过渡期允许保留，但后续应逐步瘦身或移除：

- `ExecutionHarness`
- `ContextHarness`
- `RagQueryEngine` 中直接调 capability 的分支
- `workflow nodes` 中直接调 `KnowledgeCapability` 的路径
- `container.py` 中为不同层重复 build 的 knowledge capability

## 3. 统一入口契约

### 3.1 RuntimeEntry

系统内部唯一标准提交入口：

```python
class RuntimeEntry(Protocol):
    def submit_task_spec(self, spec: TaskSpec) -> TaskRun: ...
```

约束：

- `query / chat / task` 都必须先映射为 `TaskSpec`
- 外部入口不直接调用 agent 实例
- workflow 不直接调用 runtime 内部对象

### 3.2 Request Adapter

请求适配层只负责把外部请求投影为 `TaskSpec`：

```python
class RequestAdapter(Protocol):
    def to_task_spec(self, request: Any) -> TaskSpec: ...
```

允许实现：

- `QueryRequestAdapter`
- `ChatRequestAdapter`
- `TaskRequestAdapter`

不允许在 adapter 中：

- 构造 tool
- 执行 domain service
- 写业务状态

## 4. Harness 内核契约

### 4.1 HarnessKernel

```python
class HarnessKernel(Protocol):
    def run(
        self,
        recipe: "HarnessRecipe",
        state: "HarnessState",
        ctx: "RuntimeContext",
    ) -> "HarnessResult": ...
```

职责：

- 顺序执行 stage
- 管理中断、失败、fallback、resume
- 触发 trace / checkpoint hook

不负责：

- 选择领域能力
- 构造 tool
- 理解 query/task 业务语义

### 4.2 HarnessRecipe

```python
class HarnessRecipe(Protocol):
    name: str
    def stages(self) -> list["HarnessStage"]: ...
```

职责：

- 定义执行顺序
- 声明本条链路的阶段组合
- 绑定策略与可用组件配置

允许继承扩展：

- `BaseRecipe`
- `QueryRecipe`
- `ChatRecipe`
- `DocumentAnalysisRecipe`

### 4.3 HarnessStage

```python
class HarnessStage(Protocol):
    name: str
    def run(self, state: "HarnessState", ctx: "RuntimeContext") -> "HarnessState": ...
```

职责：

- 完成单一阶段逻辑
- 消费输入 state，返回新 state

约束：

- 不允许直接 new 下游 service
- 不允许直接 build capability
- 不允许直接访问 container 全量对象

允许继承扩展：

- `BaseStage`
- `GuardrailStage`
- `RewriteStage`
- `RetrieveEvidenceStage`
- `GroundedAnswerStage`
- `FinalizeStage`

## 5. 执行上下文契约

### 5.1 RuntimeContext

```python
class RuntimeContext(BaseModel):
    request_id: str
    task_id: str | None = None
    run_id: str | None = None
    trace: Any | None = None
    tool_registry: "ToolRegistry"
    tool_executor: "ToolExecutor"
    subagent_runtime: "SubAgentRuntime | None" = None
    policy: Any | None = None
    guardrail: Any | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
```

约束：

- 只放稳定运行时依赖
- 领域临时数据不进 `RuntimeContext`
- query/task 特有数据放 `state` 或 extension config

### 5.2 HarnessState

`HarnessState` 是 step 之间共享的状态面。

建议要求：

- 结构化
- 可序列化
- 可 checkpoint
- 可 replay

不要求所有领域共用一个大而全 schema，但应至少保持以下公共字段：

- `task_spec`
- `current_stage`
- `completed_stage_ids`
- `result_contract`
- `artifacts`
- `runtime_flags`

## 6. Tool 契约

### 6.1 Tool

```python
class Tool(Protocol):
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    def run(self, payload: BaseModel, context: Any) -> BaseModel: ...
```

### 6.2 BaseTool

建议只保留薄基类：

```python
class BaseTool:
    name = ""
    version = "v1"
    timeout_ms = 30000
    risk_level = "low"
```

基类职责：

- 提供稳定元数据
- 提供通用 trace/log helper

基类不负责：

- registry 注册
- capability 构建
- fallback 策略
- 执行链编排

### 6.3 ToolRegistry

```python
class ToolRegistry(Protocol):
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    def describe(self, name: str) -> ToolSchema: ...
    def list_descriptions(self) -> list[ToolSchema]: ...
```

约束：

- 只负责查找与 schema 暴露
- 不感知 query/task 语义
- 不直接负责 fallback 决策

### 6.4 ToolExecutor

```python
class ToolExecutor(Protocol):
    def execute(self, tool_name: str, payload: dict, ctx: RuntimeContext) -> Any: ...
```

职责：

- timeout
- retry
- sandbox
- guardrail
- policy
- trace / audit

约束：

- executor 决定“怎么执行”
- recipe/stage 决定“什么时候执行”
- tool 决定“执行什么”

## 7. Sub-Agent 契约

### 7.1 BaseSubAgent

```python
class BaseSubAgent(Protocol):
    name: str
    def execute(self, action: str, payload: dict, ctx: RuntimeContext) -> Any: ...
```

### 7.2 SubAgentRuntime

```python
class SubAgentRuntime(Protocol):
    def execute(self, agent_name: str, action: str, payload: dict, ctx: RuntimeContext) -> Any: ...
```

约束：

- sub-agent 是受控执行资源
- 只允许通过 runtime 调用
- 不能成为对外系统入口
- 不能拥有独立治理策略

## 8. Domain Facade 契约

### 8.1 RagFacade

```python
class RagFacade(Protocol):
    def load_document_context(self, request: Any) -> Any: ...
    def retrieve_evidence(self, request: Any) -> Any: ...
    def grounded_answer(self, request: Any) -> Any: ...
    def grounded_query(self, request: Any) -> Any: ...
```

职责：

- 收口知识域内部复杂实现
- 对上提供稳定能力面

不允许由上层直接触达其内部子服务。

### 8.2 Capability Adapter

```python
class BaseCapabilityAdapter(Protocol):
    name: str
    def build_tools(self) -> list[Tool]: ...
```

用途：

- 把某个 domain facade 转换成一组 tool

示例：

- `RagToolAdapter`
- `RepositoryToolAdapter`
- `DatabaseToolAdapter`

## 9. RAG Tool 面契约

RAG 对 harness 暴露的标准 tool 面建议固定为：

- `rag_load_document_context`
- `rag_retrieve_evidence`
- `rag_grounded_answer`
- `rag_grounded_query`

约束：

- harness 只认这些 tool，不认 RAG 内部类
- `workflow` 和 `stage` 统一通过这些 tool 调用知识能力
- 迁移期间允许 tool 内部转调旧实现，但不允许上层直接绕过 tool

## 10. Recipe 与组合边界

### 10.1 允许继承的对象

允许扩展的类型族：

- `BaseRecipe`
- `BaseStage`
- `BaseTool`
- `BaseContextProvider`
- `BaseSubAgent`
- `BaseCapabilityAdapter`

### 10.2 不建议继承的对象

以下对象应优先保持组合式：

- `HarnessKernel`
- `ToolExecutor`
- `ToolRegistry`
- `RagFacade`
- `RuntimeEntry`

原因：

- 这些对象是运行时骨架或稳定门面
- 一旦被大量继承，很容易重新长成隐式框架

## 11. 兼容层约束

迁移期间允许保留以下兼容模式：

1. `ExecutionHarness` 作为 `ToolExecutor` facade
2. `ContextHarness` 作为 `ContextProvider` facade
3. `RagQueryEngine` 内部转调 `RagFacade`
4. 旧 workflow node 在内部改成先调 tool，再保留原 fallback

但兼容层必须遵守：

- 不再新增新职责
- 不再扩展新配置面
- 只做转发与兜底

## 12. 强约束清单

后续重构和新增功能必须满足以下硬约束：

1. 外部统一调 `submit_task_spec()`，不直接调 agent 类。
2. workflow / stage 不直接调 `KnowledgeCapability` 或 `RagQueryEngine`。
3. 新增领域能力必须通过 `Facade + ToolAdapter + Tool` 暴露。
4. 新增任务类型必须通过 `Recipe` 扩展，而不是改 kernel。
5. 基类只服务契约一致性，不承担运行编排。

## 13. 一句话结论

平台稳定面应收口为：

`RuntimeEntry -> HarnessKernel -> Recipe -> Stage -> ToolExecutor -> ToolRegistry -> Tool -> Facade`

其中：

- 扩展靠继承的是 `Recipe / Stage / Tool / SubAgent`
- 执行靠组合的是 `Recipe + Stage + Executor + ToolRegistry`
- 稳定门面是 `RuntimeEntry / ToolExecutor / Facade`
- 兼容层只允许变薄，不允许继续变胖

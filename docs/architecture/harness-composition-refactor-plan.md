# Harness 组件化与组合式执行重构方案

> 文档角色：核心设计文档
>  
> 是否建议默认加载：是
>  
> 适合回答的问题：
> - 目标架构是什么
> - `Harness / Agent / Tool / RAG` 的边界怎么收
> - 重构总体方向和阶段是什么
>  
> 建议搭配阅读：
> - `harness-runtime-contracts.md`
> - `harness-composition-migration-checklist.md`

## 1. 文档目的

本文在 [harnessed-react-agent-redesign.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harnessed-react-agent-redesign.md) 的基础上，进一步回答四个更落地的问题：

- `Harness > Agent > ReAct > Tool` 在代码里应如何收口
- `RAG` 应如何保持域内完整，同时以纯 `tool` 方式接入 harness
- 如何做到“扩展靠继承，执行靠组合”
- 如何在不打断现有 `query / chat / task` 的前提下渐进迁移

本文重点不是再次定义大方向，而是给出一版可实施的模块拆分与迁移方案。

## 2. 当前判断

当前代码的主骨架已经存在，但仍有三个明显问题：

1. `harness` 中混入了较多 `query / task` 领域语义，平台层纯度不够。
2. `KnowledgeCapability`、`RagQueryEngine`、`workflow nodes` 对检索与 grounded answer 存在重复表达。
3. `workflow`、`orchestrator`、`node` 仍会绕过 tool surface 直接触达 domain capability，导致边界不稳。

因此，这次重构不再继续扩张抽象层数，而是优先收口以下边界：

- `Harness` 只负责运行时治理与执行编排
- `Agent` 只负责任务推进与 step 选择
- `ReAct` 只负责 step 内局部动作决策
- `Tool` 只负责稳定暴露能力
- `RAG` 保持独立域能力，通过 tool adapter 接入

## 3. 重构原则

### 3.1 模块高内聚

每个模块只回答一类问题：

- `harness`：如何执行、如何治理、如何恢复
- `workflow`：这类任务应按什么步骤推进
- `tool`：如何暴露一个稳定能力面
- `domain`：这个领域能力如何真正实现

### 3.2 扩展靠继承

继承只用于“同类对象的稳定契约扩展”，例如：

- `BaseTool`
- `BaseStage`
- `BaseRecipe`
- `BaseContextProvider`
- `BaseSubAgent`

继承不应用于跨层拼装依赖，也不应用于替代运行时编排。

### 3.3 执行靠组合

任务执行顺序应由 `Recipe + Stage + Executor + ToolRegistry` 组合决定，而不是靠子类重写一整段 `run()`。

### 3.4 依赖靠注入

任何 `stage / node / tool` 都不应在内部 `build_xxx()`。所有依赖统一由 container 注入。

### 3.5 领域能力不反穿 Harness

`workflow` 和 `harness` 不应直接调用：

- `RagQueryEngine`
- `RagRetrievalService`
- `KnowledgeCapability`

如果需要知识能力，统一走注册过的 `rag_*` tool。

## 4. 目标架构

### 4.1 顶层关系

```text
Entry Layer
  -> Request Adapter
  -> TaskSpec

Harness Layer
  -> Harness Kernel
  -> Recipe
  -> Stage Pipeline
  -> Execution Context

Execution Layer
  -> Tool Executor
  -> Tool Registry
  -> SubAgent Runtime

Domain Layer
  -> RagFacade
  -> RepositoryFacade
  -> DatabaseFacade
  -> ArtifactFacade

Infra Layer
  -> Vector Store / LLM / SQLite / Sandbox / Trace
```

### 4.2 调用关系

统一调用链应收口为：

```text
HTTP / API / Service
  -> TaskSpec Adapter
  -> HarnessRuntime.submit(task_spec)
  -> Agent Runtime
  -> Recipe
  -> Stage
  -> Tool Executor
  -> Tool Registry
  -> Tool Adapter
  -> Domain Facade
```

在这个模型里：

- 外部不直接调用某个 `Agent`
- 内部不直接调用某个 `RAG` 实现类
- `Agent` 是 runtime 内部组件，而不是对外入口

## 5. Harness 的职责收口

### 5.1 Harness Core

`Harness Core` 只保留最小运行时骨架：

- 顺序执行 stage
- 处理中断、失败、fallback、resume
- 写 checkpoint、trace、audit hook
- 不感知 query/task 领域细节

建议目录：

```text
app/harness/core/
  kernel.py
  models.py
  recipe.py
  stage.py
  runtime_context.py
```

建议核心接口：

```python
class HarnessStage(Protocol):
    name: str
    def run(self, state: HarnessState, ctx: RuntimeContext) -> HarnessState: ...


class HarnessRecipe(Protocol):
    name: str
    def stages(self) -> list[HarnessStage]: ...


class HarnessKernel:
    def run(self, recipe: HarnessRecipe, state: HarnessState, ctx: RuntimeContext) -> HarnessResult: ...
```

### 5.2 Harness Components

将当前厚重的 `ExecutionHarness / ContextHarness` 拆成单责组件：

```text
app/harness/components/
  context_provider.py
  guardrail.py
  policy.py
  tool_executor.py
  reflection.py
  recovery.py
  evaluator.py
```

组件职责：

- `ContextProvider`：构造 step 可消费的上下文切片
- `Guardrail`：输入与步骤安全检查
- `Policy`：工具和预算限制
- `ToolExecutor`：统一执行 tool，处理 retry / timeout / sandbox / fallback
- `Reflector`：对本 step 输出做后验判断
- `RecoveryHandler`：决定恢复策略
- `Evaluator`：任务完成后的结构化评估

### 5.3 Harness Registry

注册中心从职责上拆开：

```text
app/harness/registry/
  tool_registry.py
  component_registry.py
  recipe_registry.py
```

其中：

- `ToolRegistry`：只负责 tool 注册、schema 暴露、查找与执行
- `ComponentRegistry`：注册上下文提供器、policy、guardrail 等
- `RecipeRegistry`：按 `task_type` 或 `entry_mode` 注册 recipe

### 5.4 Harness Extensions

`query / task` 专用能力不再放在 `harness` 根目录，而是放入扩展区：

```text
app/harness/extensions/
  query/
    recipe.py
    context_provider.py
    reflection.py
    recovery.py
  task/
    recipe.py
    context_provider.py
    evaluator.py
```

这样 `harness/core` 保持纯净，领域差异进入 extension 层。

## 6. Agent 的正确调用方式

### 6.1 外部入口不直接调 Agent

对外入口统一只接收：

- `TaskRequest`
- `QueryRequest`
- `ChatRequest`

然后全部映射为统一 `TaskSpec`，再提交到 runtime。

建议统一内部入口：

```python
class RuntimeEntry(Protocol):
    def submit_task_spec(self, spec: TaskSpec) -> TaskRun: ...
```

### 6.2 Agent 是运行时内部组件

`Agent` 的职责是“推进任务”，而不是“对外暴露服务接口”。

推荐主链：

```text
Request
  -> TaskSpec
  -> HarnessRuntime.submit_task_spec()
  -> Agent Runtime
  -> Recipe / Stage
  -> Tool / SubAgent
```

### 6.3 Sub-Agent 的位置

`SubAgent` 保持为受控执行单元：

```text
Kernel
  -> Recipe
    -> Stage
      -> Executor
        -> Tool | SubAgent
```

它不拥有独立治理权，也不作为对外入口。

## 7. RAG 的重构定位

### 7.1 RAG 保持为独立域能力

`RAG` 内部仍然可以保持完整闭环，包括：

- ingestion
- retrieval
- rerank
- grounded answer
- corrective rag
- semantic cache
- graph retrieval

这部分不应该被拆进 harness。

### 7.2 Harness 只把 RAG 当作 Tool

进入 harness 后，RAG 只通过 tool 暴露，不能直接暴露内部 service。

建议引入统一域入口：

```python
class RagFacade:
    def load_document_context(...)
    def retrieve_evidence(...)
    def grounded_answer(...)
    def grounded_query(...)
```

然后由 tool adapter 包一层：

- `rag_load_document_context`
- `rag_retrieve_evidence`
- `rag_grounded_answer`
- `rag_grounded_query`

### 7.3 为什么不是只保留一个大一统 RAG Tool

只保留一个 `rag_query` tool 会让多步 agent 丢失中间产物能力。当前系统已经有：

- evidence
- review
- artifact
- sub-agent
- workflow

因此更适合的方案是：

- 保留一个粗粒度主 tool：`rag_grounded_query`
- 保留几个细粒度辅助 tool：`retrieve_evidence / grounded_answer / load_document_context`

这样既统一，又不牺牲可组合性。

## 8. “扩展靠继承，执行靠组合”的具体落法

### 8.1 建议保留的基类

```python
class BaseTool
class BaseStage
class BaseRecipe
class BaseContextProvider
class BaseGuardrail
class BasePolicy
class BaseCapabilityAdapter
class BaseSubAgent
```

这些基类只提供：

- `name`
- `version`
- `config_schema`
- `validate()`
- 抽象 `run()`
- trace / logging hook

不在基类中承担：

- service 构建
- registry 注入
- fallback 策略细节
- workflow 路由逻辑

### 8.2 组合执行示例

```python
recipe = QueryRecipe(
    stages=[
        GuardrailStage(...),
        RewriteStage(...),
        RetrieveEvidenceStage(...),
        GroundedAnswerStage(...),
        ReflectionStage(...),
    ]
)

kernel.run(recipe, state, ctx)
```

决定运行顺序的是 `recipe`，不是某个 `Stage` 子类自己重写全流程。

## 9. 模块级重构建议

### 9.1 先拆 `ExecutionHarness`

当前 `ExecutionHarness` 过厚，建议拆分为：

- `ToolExecutor`
- `ExecutionPolicy`
- `FallbackHandler`
- `ExecutionHooks`

目标：

- 执行控制归执行器
- 策略归策略对象
- 回退归回退处理器
- trace / audit 归 hook

### 9.2 再拆 `ContextHarness`

改为：

- `BaseContextProvider`
- `QueryContextProvider`
- `TaskContextProvider`

`harness core` 不再理解 `build_query_context()` 这类领域入口。

### 9.3 收口 RAG 入口

引入：

- `RagFacade`
- `RagToolAdapter`

移除上层对以下对象的直接依赖：

- `KnowledgeCapability`
- `RagQueryEngine`
- `RagRetrievalService`

### 9.4 Workflow 只声明 Recipe，不直接调 Capability

`QueryWorkflowOrchestrator`、`DocumentAnalysisNodes` 这类对象后续应逐步演化为：

- 声明使用哪个 `Recipe`
- 注入哪些 `Stage`
- 指定允许使用哪些 tool / sub-agent

而不是直接调用某个 domain service。

## 10. 推荐目录形态

```text
app/
  harness/
    core/
    components/
    recipes/
    registry/
    extensions/
    adapters/
  agents/
    runtime.py
    planner.py
    subagents/
  domain/
    rag/
      facade.py
      services/
    repository/
    database/
    artifact/
  tools/
    base.py
    rag_tools.py
    repository_tools.py
    database_tools.py
  workflows/
    entries/
    adapters/
```

如果暂时不想引入 `domain/` 目录，也至少应做到：

- `rag` 保持单独域
- `harness` 不再直接依赖 `rag` 内部类
- `tools` 成为唯一能力接入面

## 11. 分阶段迁移计划

### Phase 1：立接口，不改行为

目标：

- 新增 `HarnessKernel / HarnessRecipe / HarnessStage`
- 新增 `RagFacade`
- 新增 `rag_tools.py`
- 老逻辑继续兼容运行

产出：

- 新旧结构可并存
- 不要求一次性删除旧路径

### Phase 2：统一 RAG 调用入口

状态更新（2026-06-23）：

- 本阶段已完成。
- `query` 与 `task/document_analysis` 两条主链已经统一通过 `rag_*` tool surface / `RagFacade` 进入知识能力。
- Phase 2 之后的工作重点应转入 Phase 3，对 `ExecutionHarness / ContextHarness` 做结构性拆分，而不是继续新增平行 RAG 入口。

目标：

- 所有 workflow / node 对 RAG 的使用改走 `rag_*` tool 或 `RagFacade`

优先改造位置：

1. `query_nodes.py`
2. `document_analysis_nodes.py`
3. `query_orchestrator.py`
4. `query_engine.py`

### Phase 3：拆薄 Harness

目标：

- `ExecutionHarness -> ToolExecutor + Policy + Hooks + Fallback`
- `ContextHarness -> ContextProvider`
- query/task 专用逻辑迁移到 `extensions`

### Phase 4：清理重复路径

目标：

- 删掉直接 build capability 的路径
- 删掉重复的检索/grounded answer 分支
- 收口 `container.py` 的重复装配

## 12. 验收标准

完成后至少应满足以下标准：

1. `workflow` 层不再直接依赖 `RagQueryEngine / KnowledgeCapability`
2. `harness/core` 中不出现 query/task 领域专用默认逻辑
3. 所有知识能力统一通过 `rag_*` tool 或 `RagFacade`
4. 新增一个 tool 时，不需要改 `HarnessKernel`
5. 新增一个 task 类型时，不需要改 runtime 主骨架，只需新增 `Recipe`
6. 新增一个领域能力时，只需补 `Facade + ToolAdapter + Tool`

## 13. 近期最小可实施切口

本轮建议先做最小切口，不直接推平全仓：

1. 新增 `app/rag/facade.py`
2. 新增 `app/tools/rag_tools.py`
3. 在 `ToolRegistry` 中注册 `rag_*` tools
4. 先把 `query_nodes.py` 中直接调用 knowledge capability 的地方改成 tool 调用
5. 保留旧路径兜底

这样做的价值最大，因为它会先把最容易混层的边界立住：

- `RAG` 仍然完整
- `Harness` 不再直连 RAG internals
- `Agent` 和 `Workflow` 的调用面变稳定

## 14. 一句话结论

这次重构的目标不是把系统再抽象一层，而是把它收成一个更稳定的执行内核：

- 类型扩展靠继承
- 任务执行靠组合
- 领域能力靠 facade 收口
- 外部接入靠 tool adapter 暴露
- `Harness` 只做运行时治理，不再承担领域实现

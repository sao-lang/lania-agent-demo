# Lania Agent Platform 架构简化方案

> 目标：消除死代码、理清分层边界、让 RAG 回归正确位置、让 Harness 回归治理本质。

---

## 1. 现状诊断

### 1.1 三条并行执行路径，只有一条在用

```
实际执行路径（唯一在用）:
  Orchestrator._invoke_workflow() → LangGraph compiled graph → 节点 → ExecutionHarness.run_tool()

死代码路径1（从未被调用）:
  Orchestrator._invoke_via_kernel() → HarnessKernel → Recipe → Stage → ExecutionHarness.run_tool()

死代码路径2（Recipe/Stage 功能已在 LangGraph 节点中重复实现）:
  QueryRecipe(GuardrailStage → RewriteStage → RetrieveEvidenceStage → ...)
```

### 1.2 RAG 的混乱定位

| 场景 | 当前实际行为 | 问题 |
|------|-------------|------|
| `POST /query` `POST /chat` | RAG 是整条管道，但通过 `ExecutionHarness(knowledge=..., rag=...)` 注入 | QueryWorkflowOrchestrator 不需要经过 Harness 访问 RAG |
| `POST /tasks` | RAG 是众多工具之一，通过 `ExecutionHarness.run_tool("rag_*")` 调用 | 这个用法是对的，但 Harness 硬编码了 rag 参数 |
| `POST /agent` | 意图识别 → 匹配 Capability → 调用 | 正常 |

### 1.3 Harness 的过度膨胀

当前 `harness/` 目录 30+ 个文件，其中约 1/3 是死代码或薄包装：

- `harness/core/` — HarnessKernel/Recipe/Stage，从未被调用
- `harness/recipes/` — Recipe 定义，功能已在 LangGraph 节点中重复
- `harness/extensions/` — 薄包装，可内联到 orchestrator
- `harness/reflection.py` / `harness/recovery.py` — 只是 re-export

### 1.4 两个 Orchestrator 的重复代码

`QueryWorkflowOrchestrator` 和 `TaskWorkflowOrchestrator` 中以下模式几乎完全相同：
- `_invoke_workflow()` — graph.invoke() 调用 + 异常处理
- `_persist_*_run()` — 持久化到内存 + SQLite
- `replay_*_run()` / `resume_*_run()` — checkpoint 恢复
- `_finalize_successful_*_state()` — 收尾事件补齐

---

## 2. 目标架构

### 2.1 分层总览

```
┌──────────────────────────────────────────────────────────────┐
│  API 层 (api/)                                                │
│  只做：JSON → Pydantic 解析 → 委托给 Service                   │
├──────────────────────────────────────────────────────────────┤
│  Service 层 (services/)                                       │
│  只做：路由转发，不执行业务逻辑                                  │
├──────────────────────┬───────────────────────────────────────┤
│  RAG 应用（独立）      │  Agent 平台（通用）                     │
│                      │                                       │
│  QueryWorkflow       │  TaskWorkflow                          │
│  Orchestrator        │  Orchestrator                          │
│    ↓                 │    ↓                                   │
│  LangGraph           │  LangGraph                             │
│  Query Graph         │  DocumentAnalysis Graph                │
│    ↓                 │    ↓                                   │
│  RagFacade           │  ExecutionHarness.run_tool("rag_*")    │
│  (直接调用)           │  ExecutionHarness.run_tool("repo_*")   │
│                      │  ExecutionHarness.run_tool("db_*")     │
│                      │                                       │
│  ★ RAG 应用内部       │  ★ 所有工具通过 ToolRegistry 统一调用    │
│  不经过 Harness       │  ★ Harness 不知道具体 Capability 是什么  │
├──────────────────────┴───────────────────────────────────────┤
│  Harness 治理层                                               │
│  ExecutionHarness · ContextHarness · EvaluationHarness        │
│  GroundingEngine · PromptBuilder · PolicyEngine               │
│  GuardrailEngine · ToolSandbox · EventBus · ReActRuntime      │
├──────────────────────────────────────────────────────────────┤
│  Domain 层 (capabilities/ + rag/)                             │
│  KnowledgeCapability · RagFacade · RepositoryCapability ...   │
├──────────────────────────────────────────────────────────────┤
│  Infra 层 (core/ + services/state.py + sqlite_store.py)       │
│  LLM · VectorStore · SQLite · Sandbox                         │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 核心原则

1. **RAG 是独立应用 + 也是 Tool** — `/query` `/chat` 端点走自己的 RAG 管道，不经过 Harness；同时 RAG 能力注册为 Tool，供 Agent 平台调用
2. **LangGraph 是唯一的 workflow engine** — 不在其上叠加 Recipe/Stage/HarnessKernel 抽象
3. **Harness = 横切治理层** — 工具执行的 policy/guardrail/sandbox/retry/circuit_breaker，不包含 workflow 编排
4. **ExecutionHarness 只依赖 ToolRegistry** — 不 import 任何具体 Capability

---

## 3. 实施步骤

### Step 1: 删除死代码（零风险，纯删除）

删除以下文件和目录：

```
app/harness/core/kernel.py          # HarnessKernel — 只被 _invoke_via_kernel() 引用
app/harness/core/recipe.py          # BaseRecipe + RecipeRegistry
app/harness/core/stage.py           # BaseStage
app/harness/core/__init__.py        # 导出 HarnessKernel/BaseRecipe/BaseStage，随之一同删除
app/harness/recipes/                # 整个目录（3 个文件）
```

> **注意**：`harness/extensions/`、`harness/reflection.py`、`harness/recovery.py` 不在这一步删除。
> `extensions/query/reflection.py` 和 `extensions/query/recovery.py` 被 orchestrator 和 nodes 实际使用，
> 需要先迁移到 `harness/` 根目录（见 Step 2.5）。

同时删除两个 Orchestrator 中的死代码方法：

```python
# query_orchestrator.py — 删除
def _build_ctx(self) -> dict[str, Any]:      # 第 572-588 行
def _invoke_via_kernel(self, ...):            # 第 590-637 行

# task_orchestrator.py — 删除
def _build_ctx(self) -> dict[str, Any]:      # 第 276-294 行
def _invoke_via_kernel(self, ...):            # 第 296-336 行
```

删除对应的 import：
- `from app.harness.core.kernel import HarnessKernel`
- `from app.harness.recipes.query_recipe import ...`
- `from app.harness.recipes.task_recipe import ...`

### Step 2: 迁移 `core/` 中仍被引用的模块

将 `hooks.py`、`trace_hook.py`、`prompt_registry.py` 从 `core/` 移到 `harness/` 根目录：

```
app/harness/core/hooks.py           → app/harness/hooks.py
app/harness/core/trace_hook.py      → app/harness/trace_hook.py
app/harness/core/prompt_registry.py → app/harness/prompt_registry.py
```

更新所有 import 路径（共 20 处）：

| 原路径 | 新路径 | 引用文件 |
|--------|--------|---------|
| `app.harness.core.hooks` | `app.harness.hooks` | container.py, execution.py, execution_hooks.py, query_orchestrator.py, task_orchestrator.py |
| `app.harness.core.hooks` | `app.harness.hooks` | trace_hook.py（内部引用，第 12、102 行） |
| `app.harness.core.trace_hook` | `app.harness.trace_hook` | container.py, execution.py |
| `app.harness.core.prompt_registry` | `app.harness.prompt_registry` | prompting.py |
| `app.harness.core.kernel` | （删除） | query_orchestrator.py, task_orchestrator.py |

删除 `app/harness/core/` 目录（搬迁后只剩 `hooks.py`、`trace_hook.py`、`prompt_registry.py`，迁移后目录为空）。

### Step 2.5: 迁移 `extensions/query/` 内容到 `harness/` 根目录

`extensions/query/reflection.py` 和 `extensions/query/recovery.py` 被 `query_orchestrator.py`、`query_nodes.py`、`query_graph.py` 实际使用，是**错误现场恢复**和**反思决策**的核心代码，不能删除。

当前结构：
```
harness/extensions/query/reflection.py  ← 实际实现（ReflectionHarness，约 80 行）
harness/extensions/query/recovery.py    ← 实际实现（RecoveryManager，约 64 行）
harness/reflection.py                   ← 薄包装：只是 `from extensions.query.reflection import ReflectionHarness`
harness/recovery.py                     ← 薄包装：只是 `from extensions.query.recovery import RecoveryManager`
```

迁移方案：把实际实现搬到 `harness/` 根目录，替换掉薄包装文件：

```
harness/reflection.py  ← 替换为 extensions/query/reflection.py 的实际内容
harness/recovery.py    ← 替换为 extensions/query/recovery.py 的实际内容
harness/extensions/    ← 删除整个目录
```

更新 import 路径（共 4 处）：

| 原路径 | 新路径 | 引用文件 |
|--------|--------|---------|
| `app.harness.extensions.query.recovery` | `app.harness.recovery` | query_orchestrator.py |
| `app.harness.extensions.query.reflection` | `app.harness.reflection` | query_orchestrator.py, query_nodes.py, query_graph.py |

> **注意**：`harness/__init__.py` 已通过懒加载引用 `app.harness.recovery` 和 `app.harness.reflection`，迁移后路径不变，无需修改。

### Step 3: 改造 ExecutionHarness（去掉 Capability 硬编码）

**文件**: `app/harness/execution.py`

**改动**：
- 删除 6 个 Capability import（`RagFacade`, `KnowledgeCapability`, `RepositoryCapability`, `ApiContractCapability`, `ArtifactCapability`, `DatabaseCapability`）
- 删除 6 个 Capability 参数 + `build_*` 回退逻辑（第 55-95 行）
- 新增 `capabilities: dict[str, Any] | None = None` 参数
- `ExecutionRuntimeDependencies` 构造时传 `capabilities=self.capabilities`
- **不需要** `__getattr__` — 全仓库零 `harness.xxx` 访问 Capability，工具只通过 `context.xxx` 访问

**文件**: `app/harness/components/tool_executor.py`

**改动**：
- 删除 6 个硬编码字段（`knowledge`, `rag`, `repository`, `api_contract`, `artifact`, `database`）
- 新增 `capabilities: dict[str, Any] | None = None` 字段
- `_tool_context()` 方法：`deps=self.dependencies.capabilities or {}`

**文件**: `app/agents/tools/base.py`

**改动**：
- 删除 6 个 Capability 相关 import
- 删除 6 个硬编码字段
- 新增 `deps: dict[str, Any] | None = None` 字段
- 新增 `__getattr__` 方法，委托到 `deps` dict

```python
@dataclass
class ToolContext:
    state: InMemoryState
    retrieval: Any
    trace: TraceRecorder
    task_memory: TaskMemory
    settings: Settings
    llm: Any | None = None
    vector_store: Any | None = None
    deps: dict[str, Any] | None = None          # ← 替代 6 个硬编码字段
    services: dict[str, Any] | None = None
    task_id: str | None = None
    step_name: str | None = None
    tool_call_id: str | None = None
    run_budget: Any | None = None
    model_router: ModelRouter | None = None

    def __getattr__(self, name: str) -> Any:
        if name == 'deps' or name.startswith('__'):
            raise AttributeError(name)
        if self.deps is not None and name in self.deps:
            return self.deps[name]
        raise AttributeError(f"'ToolContext' has no attribute '{name}'")
```

### Step 4: 改造 Container（统一组装 capabilities）

**文件**: `app/container.py`

在 Capability 构建完成后，组装 `capabilities` dict：

```python
self.capabilities = {
    'knowledge': self.knowledge_capability,
    'rag': self.rag_facade,
    'repository': self.repository_capability,
    'api_contract': self.api_contract_capability,
    'artifact': self.artifact_capability,
    'database': self.database_capability,
}
```

传给 Orchestrator 时替换两处（第 191-192 行和第 364-365 行）：

```python
# 修改前
QueryWorkflowOrchestrator(..., knowledge_capability=..., rag_facade=..., ...)
TaskWorkflowOrchestrator(..., knowledge_capability=..., rag_facade=..., ...)

# 修改后
QueryWorkflowOrchestrator(..., capabilities=self.capabilities, ...)
TaskWorkflowOrchestrator(..., capabilities=self.capabilities, ...)
```

> **注意**：`RagQueryEngine`（第 182 行）仍接收 `knowledge_capability` 参数，它是 RAG 域组件，不经过 Harness，不需要改为 `capabilities` dict。

### Step 5: 改造 QueryWorkflowOrchestrator（RAG 不经过 Harness）

**文件**: `app/workflows/query_orchestrator.py`

**改动**：
- `__init__` 签名：用 `capabilities: dict[str, Any] | None` 替代 `knowledge_capability` + `rag_facade` 两个独立参数
- 从 `capabilities` dict 中提取 `self.knowledge_capability = capabilities['knowledge']` 和 `self.rag_facade = capabilities['rag']`
- 删除 `getattr(execution_harness, 'knowledge', None)` 和 `getattr(execution_harness, 'rag', None)` 回退逻辑（第 133、166-169 行）
- `ExecutionHarness` 构造时传 `capabilities=self.capabilities`（不再单传 `knowledge=...`, `rag=...`）
- 删除已废弃的 `knowledge_capability` 和 `rag_facade` 参数

### Step 6: 改造 TaskWorkflowOrchestrator（同上）

**文件**: `app/workflows/tasks/task_orchestrator.py`

**改动**：
- `__init__` 签名：用 `capabilities: dict[str, Any] | None` 替代 `knowledge_capability` + `rag_facade` 两个独立参数
- 从 `capabilities` dict 中提取 `self.knowledge_capability = capabilities['knowledge']` 和 `self.rag_facade = capabilities['rag']`
- 删除 `getattr(execution_harness, 'knowledge', None)` 和 `getattr(execution_harness, 'rag', None)` 回退逻辑（第 99、114、133-136 行）
- `ExecutionHarness` 构造时传 `capabilities=self.capabilities`（不再单传 `knowledge=...`, `rag=...`）
- 删除已废弃的 `knowledge_capability` 和 `rag_facade` 参数

### Step 7: 抽取 WorkflowRunner 基类（消除重复代码）

**新建文件**: `app/workflows/runner.py`

```python
class WorkflowRunner:
    """LangGraph 工作流运行生命周期的薄封装。
    
    不封装 LangGraph 的图 API（node/edge/route），
    只封装"调用 graph → 持久化 → checkpoint → replay"这套通用模式。
    """
    
    def run(self, graph, initial_state, *, initial_route=None) -> dict:
        """调用 graph.invoke()，统一异常处理和持久化。"""
        ...
    
    def replay(self, graph, checkpoint) -> dict:
        """从 checkpoint 恢复并继续执行。"""
        ...
    
    def persist(self, state) -> None:
        """持久化到内存 + SQLite。"""
        ...
    
    # 子类实现：
    def build_record(self, state) -> dict: ...
    def build_summary(self, record) -> BaseModel: ...
    def finalize(self, state) -> None: ...
```

两个 Orchestrator 继承 `WorkflowRunner`，删除重复的 `_invoke_workflow`、`_persist_*`、`replay_*`、`resume_*` 方法。

### Step 8: 更新测试

**文件**: `tests/test_harness_runtime.py`, `tests/test_tool_registry.py`, `tests/test_task_llm_tools.py`

- `test_harness_runtime.py`：`ExecutionHarness(...)` 加 `capabilities={}`
- `test_tool_registry.py`：`ToolContext(...)` 的 6 个 Capability 字段改为 `deps={...}`
- `test_task_llm_tools.py`：无需改动（未传 Capability 字段）

---

## 4. 依赖顺序

```
Step 1 (删除死代码)           ← 零风险，可立即执行
  → Step 2 (迁移 core/ 模块)  ← 改 import 路径
    → Step 3 (改造 ToolContext + ExecutionRuntimeDependencies + ExecutionHarness)
      → Step 4 (改造 container.py)
        → Step 5 + Step 6 (改造两个 Orchestrator)  [可并行]
          → Step 7 (抽取 WorkflowRunner)            [可选，后续优化]
            → Step 8 (更新测试)
```

---

## 5. 文件变更汇总

### 删除（约 14 个文件）

```
app/harness/core/              (7 个文件，含空 __init__.py)
app/harness/recipes/           (3 个文件)
app/harness/extensions/        (4 个文件，内容已迁移到 harness/ 根目录)
```

### 迁移（5 个文件）

```
app/harness/core/hooks.py           → app/harness/hooks.py           (3 个文件从 core/ 移出)
app/harness/core/trace_hook.py      → app/harness/trace_hook.py
app/harness/core/prompt_registry.py → app/harness/prompt_registry.py

app/harness/extensions/query/reflection.py → app/harness/reflection.py  (替换薄包装)
app/harness/extensions/query/recovery.py   → app/harness/recovery.py    (替换薄包装)
```

### 修改（约 10 个文件）

```
app/agents/tools/base.py                     # ToolContext: 加 deps dict + __getattr__
app/harness/components/tool_executor.py       # ExecutionRuntimeDependencies: 加 capabilities dict
app/harness/execution.py                      # ExecutionHarness: 去掉 Capability 硬编码
app/container.py                              # 组装 capabilities dict
app/workflows/query_orchestrator.py           # 删除死代码 + 用 capabilities dict
app/workflows/tasks/task_orchestrator.py      # 删除死代码 + 用 capabilities dict
tests/test_harness_runtime.py                 # 适配新签名
tests/test_tool_registry.py                   # 适配新 ToolContext
```

### 新建（1 个文件）

```
app/workflows/runner.py                       # WorkflowRunner 基类
```

---

## 6. 简化后的 `harness/` 目录

```
app/harness/
├── execution.py              # ExecutionHarness：工具执行治理入口
├── context.py                # ContextHarness：上下文构建入口
├── hooks.py                  # EventBus + HookRegistry（从 core/ 迁移）
├── trace_hook.py             # TraceHook + MemoryHook（从 core/ 迁移）
├── prompting.py              # PromptBuilder：提示词管理
├── prompt_registry.py        # PromptVersionRegistry（从 core/ 迁移）
├── reflection.py             # ReflectionHarness（从 extensions/ 迁移，替换薄包装）
├── recovery.py               # RecoveryManager（从 extensions/ 迁移，替换薄包装）
├── policy.py                 # PolicyEngine
├── guardrails.py             # GuardrailEngine
├── sandbox.py                # ToolSandbox
├── model_router.py           # ModelRouter
├── react_runtime.py          # BoundedLocalReActRuntime
├── evaluation.py             # EvaluationHarness
├── grounding.py              # GroundingEngine
├── selection.py              # SelectionEngine
├── compression.py            # CompressionEngine
├── budgeting.py              # TokenBudgetEngine
├── context_policy.py         # ContextPolicy
├── models.py                 # 数据模型
└── components/
    ├── tool_executor.py      # ToolExecutor
    ├── execution_hooks.py    # ExecutionHooks
    ├── execution_policy.py   # ExecutionPolicyResolver
    ├── fallback_handler.py   # FallbackHandler
    ├── context_builders.py   # TaskContextBuilder + QueryContextBuilder
    ├── context_models.py     # ContextOptimizationResult
    ├── guardrail_checks.py   # Guardrail 检查
    ├── guardrail_raiser.py   # Guardrail 异常
    ├── policy_checks.py      # Policy 检查
    └── policy_profiles.py    # Policy 配置
```

---

## 7. 验证

```bash
# 1. 确认 Harness 零 Capability 导入
grep -rn "from app.capabilities" app/harness/
grep -rn "from app.rag.facade" app/harness/
# 预期：零结果

# 2. 确认死代码已删除
grep -rn "HarnessKernel\|BaseRecipe\|BaseStage\|QueryRecipe\|ChatRecipe" app/
# 预期：零结果（除文档外）

# 3. 运行测试
pytest tests/test_harness_runtime.py tests/test_tool_registry.py tests/test_task_llm_tools.py -v

# 4. 确认工具仍能访问 Capability
grep -rn "context\.\(rag\|knowledge\|repository\|api_contract\|artifact\|database\)" app/agents/tools/
# 预期：访问模式不变，通过 __getattr__ 工作
```

---

## 8. 风险

| 风险 | 级别 | 缓解 |
|------|------|------|
| `__getattr__` + `@dataclass` 可能破坏 `copy`/`pickle` | 低 | `ToolContext` 当前不序列化或深拷贝 |
| `hasattr(context, "repository")` 失效 | 低 | `hasattr` 触发 `__getattr__`，在 `deps` 中查找，行为正确 |
| 测试依赖 `build_*` 回退 | 低 | 测试不实际使用 Capability，传 `capabilities={}` 即可 |
| `TraceRecorder` 仍在 `app/rag/` 下 | 中 | 横切关注点，后续单独重构移到 `app/core/` |
| 删除 `extensions/` 影响 reflection/recovery 功能 | 无 | 实际实现已迁移到 `harness/reflection.py` 和 `harness/recovery.py`，功能完整保留 |

---

## 9. 功能完整性

| 功能 | 是否保留 | 说明 |
|------|---------|------|
| RAG 查询 (`/query`, `/chat`) | ✅ | `QueryWorkflowOrchestrator` → LangGraph → `RagFacade` |
| 文档分析任务 (`/tasks`) | ✅ | `TaskWorkflowOrchestrator` → LangGraph → `ExecutionHarness.run_tool()` |
| Agent 对话 (`/agent`) | ✅ | `AgentService` → 意图识别 → Capability 路由 |
| 工具执行治理 | ✅ | `ExecutionHarness` 保留 policy/guardrail/sandbox/retry/circuit_breaker |
| 上下文构建 | ✅ | `ContextHarness` → `context_builders` |
| 证据 Grounding | ✅ | `GroundingEngine` |
| 任务评估 | ✅ | `EvaluationHarness` |
| Prompt 管理 | ✅ | `PromptBuilder` + `PromptVersionRegistry` |
| 事件总线 | ✅ | `EventBus` + `TraceHook` |
| ReAct 运行时 | ✅ | `BoundedLocalReActRuntime` |
| Checkpoint/Replay/Resume | ✅ | 在 `WorkflowRunner` 基类中统一处理 |
| 记忆系统 | ✅ | `TaskMemory` + `MemoryCommitGate` |
| 所有 Tool | ✅ | `ToolRegistry` 不变 |
| 所有 Capability | ✅ | `capabilities/` 目录不变 |
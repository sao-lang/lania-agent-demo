# Harness 解耦重构计划

## 背景

当前 Harness 层（`app/harness/`）有 59 处跨域 import，违反了"领域能力不反穿 Harness"的设计原则。核心问题：

1. **`execution.py`** — `ExecutionHarness.__init__()` 硬编码了 6 个 Capability 参数 + 各自的 `build_*` 回退函数
2. **`tool_executor.py`** — `ExecutionRuntimeDependencies` 硬编码了 6 个 Capability 字段
3. **`base.py`** — `ToolContext` 硬编码了 6 个 Capability 字段

依赖方向反了：`Harness → RAG/Capabilities`，而不是 `RAG/Capabilities → Harness`。

## 目标

- Harness 不再 import 任何 `app/capabilities/` 或 `app/rag/facade` 的符号
- 新增 Capability **不需要改任何 Harness 文件**
- 工具仍可通过 `context.rag`、`context.knowledge` 等访问能力（向后兼容）
- 依赖方向：`Domain → Harness`（正确方向）

## 核心思路

**用 `dict[str, Any]` 替代硬编码字段**。`ToolContext` 和 `ExecutionRuntimeDependencies` 用 `deps` / `capabilities` dict 取代逐个 Capability 字段，`__getattr__` 委托实现向后兼容的 `context.rag` 访问。

## 实施步骤

### Step 1: 改造 `ToolContext`（基础层）

**文件**: `app/agents/tools/base.py`

- 删除 6 个 Capability 相关的 import（`RagFacade`, `KnowledgeCapability`, `RepositoryCapability`, `ApiContractCapability`, `ArtifactCapability`, `DatabaseCapability`）
- 删除 6 个硬编码字段（`rag`, `knowledge`, `repository`, `api_contract`, `artifact`, `database`）
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
    deps: dict[str, Any] | None = None          # ← 新字段
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

**关键保证**: `@dataclass` 的字段在 `__init__` 中通过 `object.__setattr__` 直接设置，不走 `__getattr__`。运行时对 `context.rag` 的访问会触发 `__getattr__` → `deps['rag']`。`hasattr(context, "repository")` 仍然正常工作。

### Step 2: 改造 `ExecutionRuntimeDependencies`

**文件**: `app/harness/components/tool_executor.py`

- 删除 6 个硬编码字段（`knowledge`, `rag`, `repository`, `api_contract`, `artifact`, `database`）
- 新增 `capabilities: dict[str, Any] | None = None` 字段
- 更新 `_tool_context()` 方法：`deps=self.dependencies.capabilities or {}`

```python
@dataclass
class ExecutionRuntimeDependencies:
    state: Any
    retrieval: Any
    trace: Any
    task_memory: Any
    settings: Settings
    llm: Any
    vector_store: Any
    capabilities: dict[str, Any] | None = None   # ← 替代 6 个字段
    model_router: Any
    services: dict[str, Any] | None = None
```

### Step 3: 改造 `ExecutionHarness`

**文件**: `app/harness/execution.py`

- 删除 6 个 Capability import（第 18-22 行）+ `RagFacade` import（第 35 行）
- 保留 `TraceRecorder` import（横切关注点，后续单独处理）
- 用 `capabilities: dict[str, Any] | None = None` 替代 6 个参数
- 删除 `build_*` 回退逻辑（第 78-95 行）
- 新增 `__getattr__` 向后兼容（`harness.knowledge` → `capabilities['knowledge']`）
- 更新 `ExecutionRuntimeDependencies` 构造

### Step 4: 改造 `container.py`

**文件**: `app/container.py`

- 在所有 Capability 构建完成后，组装 `capabilities` dict
- 传给 `TaskWorkflowOrchestrator` 和 `QueryWorkflowOrchestrator`

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

### Step 5: 改造 `TaskWorkflowOrchestrator`

**文件**: `app/workflows/tasks/task_orchestrator.py`

- `__init__` 签名：用 `capabilities: dict[str, Any] | None = None` 替代 `knowledge_capability` + `rag_facade` 两个参数
- `ExecutionHarness` 构造时传 `capabilities=self.capabilities`
- 从 `capabilities` dict 中取 `self.knowledge_capability` 和 `self.rag_facade`

### Step 6: 改造 `QueryWorkflowOrchestrator`

**文件**: `app/workflows/query_orchestrator.py`

- `ExecutionHarness` 构造时传 `capabilities={'knowledge': ..., 'rag': ...}`
- `_build_ctx()` 无需改动（已经传整体 harness 对象）

### Step 7: 更新测试

**文件**: `tests/test_harness_runtime.py`, `tests/test_tool_registry.py`, `tests/test_task_llm_tools.py`

- `test_harness_runtime.py`：`ExecutionHarness(...)` 加 `capabilities={}`
- `test_tool_registry.py`：`ToolContext(...)` 的 6 个 Capability 字段改为 `deps={...}`
- `test_task_llm_tools.py`：无需改动（未传 Capability 字段）

## 依赖顺序

```
Step 1 (ToolContext) 
  → Step 2 (ExecutionRuntimeDependencies) 
    → Step 3 (ExecutionHarness)
      → Step 4 (container.py) + Step 5 (task_orchestrator) + Step 6 (query_orchestrator)  [可并行]
        → Step 7 (tests)
```

## 风险

| 风险 | 级别 | 缓解 |
|------|------|------|
| `__getattr__` + `@dataclass` 可能破坏 `copy`/`pickle` | 低 | `ToolContext` 当前不序列化或深拷贝 |
| `hasattr(context, "repository")` 失效 | 低 | `hasattr` 触发 `__getattr__`，在 `deps` 中查找，行为正确 |
| 测试依赖 `build_*` 回退 | 低 | 测试不实际使用 Capability，传 `capabilities={}` 即可 |
| `TraceRecorder` 仍在 `app/rag/` 下 | 中 | 横切关注点，单独重构移到 `app/core/`，本次不改 |

## 验证

```bash
# 1. 确认 Harness 零 Capability 导入
grep -rn "from app.capabilities" app/harness/
grep -rn "from app.rag.facade" app/harness/
# 预期：零结果

# 2. 运行测试
pytest tests/test_harness_runtime.py tests/test_tool_registry.py tests/test_task_llm_tools.py -v

# 3. 确认工具仍能访问 Capability
grep -rn "context\.\(rag\|knowledge\|repository\|api_contract\|artifact\|database\)" app/agents/tools/
# 预期：访问模式不变，通过 __getattr__ 工作
```
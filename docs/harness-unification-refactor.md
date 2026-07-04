# 全栈重构方案：Harness 双线合一 + 记忆系统改造

> 本文档是两份重构方案的合并版：
> 1. **Harness 双线合一**：Recipe/Stage/Kernel（死代码）和 LangGraph（实际运行）合并为 Hybrid 管线
> 2. **记忆系统改造**：SessionManager 持久化、UserProfile、MemoryCommitGate、5 层记忆全面落地
>
> 两份重构有依赖关系——MemoryHook 依赖 Harness 的 EventBus 统一后才能激活，因此按依赖顺序排列为 6 个 Phase。

---

## 1. 现状分析

### 1.1 两条并行的 Harness 执行线

```
文档设计的线（死代码）             实际运行的线（LangGraph）
─────────────────────────         ─────────────────────────

HarnessKernel.run(recipe)         LangGraph StateGraph.invoke()
  │                                  │
  ├─ GuardrailStage.run()            ├─ check_guardrails(state)
  ├─ RewriteStage.run()              ├─ rewrite_query(state)
  ├─ RetrieveEvidenceStage.run()     ├─ retrieve_evidence(state)
  │    └─ (空实现，返回 state)        │    └─ ExecutionHarness.run_tool()
  ├─ GroundedAnswerStage.run()       ├─ grounded_answer(state)
  ├─ ReflectionStage.run()           ├─ self_reflect(state)
  └─ FinalizeStage.run()             └─ finalize(state)
```

**两条线各自独立，互不调用**。文档线的所有 Stage `run()` 均为空实现。实际线的所有节点通过 `ExecutionHarness` / `ContextHarness` 执行真实逻辑。

### 1.2 三个孤立的记忆系统

```
SessionManager（纯内存）
  └─ _sessions: dict[str, Session]  ← 重启丢失
  └─ 不读写 InMemoryState / SQLiteStateStore

TaskMemory（主记忆系统）
  └─ memory_records: list[MemoryRecord]  ← 上限 200
      ├─ scope='working' ✓      scope='session' ✗
      ├─ scope='run'     ✓      scope='semantic' ✗
      └─ trust_level 固定不变   scope='profile'  ✗

UserProfile（不存在）
  └─ 没有模型，没有存储，没有画像
```

### 1.3 文件状态总览

| 文件 | 行数 | 归属 | 状态 |
|------|------|------|------|
| `core/kernel.py` | 123 | Harness | ✅ 完整实现，**零调用** |
| `core/recipe.py` | 98 | Harness | ✅ 完整实现，**零调用** |
| `core/stage.py` | 57 | Harness | ✅ 完整实现，**零调用** |
| `core/hooks.py` | 167 | Harness | ✅ 完整，EventBus 部分使用 |
| `core/trace_hook.py` | 103 | Harness | ✅ TraceHook 活跃，**MemoryHook 零调用** |
| `core/runtime_context.py` | 24 | Harness | ✅ 完整，**零导入** |
| `core/sandbox_extensions.py` | 118 | Harness | ✅ 完整，**零调用** |
| `recipes/query_recipe.py` | 120 | Harness | ✅ 6 个 Stage，**`run()` 全空** |
| `recipes/task_recipe.py` | 120 | Harness | ✅ 8 个 Stage，**`run()` 全空** |
| `recipes/__init__.py` | 50 | Harness | ✅ 工厂函数，**零调用** |
| `services/session_manager.py` | 120 | Memory | ✅ 完整，**纯内存不持久化** |
| `services/state.py` | ~50 | Memory | ✅ 完整，sessions 表未使用 |
| `services/sqlite_store.py` | ~400 | Memory | ✅ 完整，sessions 表有但 SessionManager 不用 |
| **合计问题代码** | **~1500 行** | | |

---

## 2. 目标架构

### 2.1 四层统一模型

```
Recipe/Stage（声明层）
  │  定义：流程步骤、工具使用、条件路由
  │  来源：app/harness/recipes/*.py
  │  变更：从空实现改为真实业务逻辑

HarnessKernel（编排 + 治理层）
  │  职责：接收 Recipe → 构建 LangGraph 图 → 注入 EventBus/Hook
  │  来源：app/harness/core/kernel.py
  │  变更：从顺序 iterator 重写为 LangGraph graph builder

LangGraph StateGraph（图执行层）
  │  职责：状态管理、条件路由、循环、checkpoint、重入
  │  节点：自动由 HarnessKernel 从 Recipe 生成
  │  变更：不再需要手动维护 graph 文件

Unified Memory Bus（记忆总线层）
  │  职责：5 层记忆（working/session/run/semantic/profile）
  │  来源：TaskMemory + MemoryCommitGate + UserProfileService
  │  变更：SessionManager 持久化、UserProfile 新增、MemoryHook 激活
```

### 2.2 改造前后对比

```
改造前：                              改造后：

LangGraph.invoke()                   HarnessKernel.run(recipe, ...)
  │                                     │
  ├─ 手动定义 graph 拓扑                ├─ 自动从 Recipe 构建 StateGraph
  ├─ 节点含 EventBus 不全               ├─ 统一 EventBus 治理
  ├─ 条件路由硬编码在 graph 边中         ├─ 条件路由来自 Stage.route_next()
  ├─ Recipe 是死代码                    ├─ Recipe 是真实逻辑入口
  └─ Memory 只有 2/5 层                └─ 5 层记忆全面激活

SessionManager（纯内存）               SessionManager（持久化）
  └─ 重启丢失                          └─ InMemoryState + SQLite 双写
                                       └─ 同步写 MemoryRecord(scope='session')

UserProfile（不存在）                   UserProfileService（新增）
  └─ 无                                └─ SQLite 持久化 + 偏好推断
                                       └─ 写 MemoryRecord(scope='profile')

MemoryHook（零调用）                    MemoryHook（激活）
  └─ 死代码                            └─ EventBus 事件 → 清理 working
                                       └─ EventBus 事件 → 写 memory_records
```

### 2.3 最终执行流

```
Orchestrator
  │
  ├─ 1. RecipeRegistry.get_by_task_type() → Recipe
  │
  ├─ 2. HarnessKernel.run(recipe, state, ctx)
  │      │
  │      ├─ EventBus.run_started()
  │      │    └─ TraceHook → TraceRecorder
  │      │    └─ MemoryHook → TaskMemory.append_memory_record()
  │      │
  │      ├─ 从 recipe.stages() 构建 StateGraph
  │      │
  │      ├─ app.invoke(state)    ← LangGraph 执行
  │      │    │
  │      │    ├─ 每个 Node 内：
  │      │    │   ├─ EventBus.before_stage()
  │      │    │   ├─ Stage.run() → 真实业务逻辑
  │      │    │   │    └─ ExecutionHarness.run_tool()
  │      │    │   │         ├─ EventBus.before_tool()
  │      │    │   │         ├─ GuardrailEngine
  │      │    │   │         ├─ PolicyEngine
  │      │    │   │         ├─ SandboxEngine
  │      │    │   │         ├─ ToolExecutor (retry/circuit/timeout)
  │      │    │   │         └─ EventBus.after_tool()
  │      │    │   ├─ Stage.route_next() → 条件路由
  │      │    │   └─ EventBus.after_stage()
  │      │    │        └─ MemoryHook._clear_working_memory()
  │      │    │        └─ Checkpoint (if creates_checkpoint_after)
  │      │    │
  │      │    └─ 循环/分支由 LangGraph StateGraph 管理
  │      │
  │      ├─ EventBus.run_completed()
  │      └─ 返回 HarnessResult
  │
  ├─ 3. 从 result.state.payload 组装响应
  │
  ├─ 4. MemoryCommitGate.commit_to_semantic()
  │     将已验证的 run 记录晋升为 semantic (跨任务复用)
  │
  └─ 5. UserProfileService.infer_and_update()
          从交互推断用户偏好
```

---

## 3. 详细设计

### 3.1 HarnessKernel：从顺序执行器重写为 LangGraph Graph Builder

这是 Harness 重构最核心的改动。

```python
# app/harness/core/kernel.py (重写)

from langgraph.graph import StateGraph
from app.harness.core.hooks import EventBus
from app.harness.core.stage import BaseStage


class HarnessState(dict):
    """LangGraph 状态模型，直接继承 dict 兼容 StateGraph 协议。"""
    payload: dict = {}
    current_stage: str | None = None
    completed_stage_ids: list[str] = []
    stage_errors: dict[str, str] = {}
    checkpoints: list[dict] = []


def _build_stage_node(stage: BaseStage, ctx: dict, event_bus: EventBus):
    """将单个 Stage 包装为 LangGraph 节点函数。"""
    def _node(state: HarnessState) -> HarnessState:
        stage_name = stage.name
        ws = state.get('_workflow_state')

        # 1. before_stage
        event_bus.before_stage(ws, stage_name=stage_name)

        # 2. 输入校验
        if hasattr(stage, 'validate_input'):
            issues = stage.validate_input(state.get('payload', {}), ctx)
            if issues:
                state['stage_errors'][stage_name] = '; '.join(issues)
                event_bus.stage_failed(ws, stage_name=stage_name, error=state['stage_errors'][stage_name])
                return state

        # 3. 执行业务逻辑
        try:
            new_payload = stage.run(dict(state.get('payload', {})), ctx)
            state['payload'] = new_payload
            state['completed_stage_ids'] = state.get('completed_stage_ids', []) + [stage_name]
        except Exception as exc:
            state['stage_errors'][stage_name] = str(exc)
            event_bus.stage_failed(ws, stage_name=stage_name, error=str(exc))
            return state

        # 4. 输出校验
        if hasattr(stage, 'validate_output'):
            issues = stage.validate_output(new_payload)
            if issues:
                state['stage_errors'][stage_name] = '; '.join(issues)
                event_bus.stage_failed(ws, stage_name=stage_name, error=state['stage_errors'][stage_name])
                return state

        # 5. 按需 checkpoint
        if stage.creates_checkpoint_after:
            cp = {
                'checkpoint_id': f'cp-{stage_name}-{len(state.get("checkpoints", []))}',
                'stage': stage_name,
                'payload_snapshot': dict(state.get('payload', {})),
            }
            state.setdefault('checkpoints', []).append(cp)
            event_bus.after_checkpoint(ws, checkpoint_id=cp['checkpoint_id'])

        # 6. after_stage（MemoryHook 在此处清理 working 记忆）
        event_bus.after_stage(ws, stage_name=stage_name)
        return state

    return _node


def _build_route_condition(stage: BaseStage):
    """为支持条件路由的 Stage 生成路由函数。"""
    if hasattr(stage, 'route_next') and callable(stage.route_next):
        return stage.route_next
    return None


class HarnessKernel:
    """接收 Recipe，构建并执行 LangGraph 图。"""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self.event_bus = event_bus or EventBus()

    def run(
        self,
        recipe: Any,
        state: dict[str, Any],
        ctx: dict[str, Any],
        *,
        workflow_state: dict[str, Any] | None = None,
    ) -> 'HarnessResult':
        state['_workflow_state'] = workflow_state or {}
        state.setdefault('payload', {})
        state.setdefault('completed_stage_ids', [])
        state.setdefault('stage_errors', {})
        state.setdefault('checkpoints', [])

        self.event_bus.run_started(workflow_state, recipe_name=getattr(recipe, 'name', str(recipe)))

        # 构建 StateGraph
        graph = StateGraph(HarnessState)
        stages = recipe.stages()

        for stage in stages:
            node_id = f'stage_{stage.name}'
            graph.add_node(node_id, _build_stage_node(stage, ctx, self.event_bus))

        for i, stage in enumerate(stages):
            node_id = f'stage_{stage.name}'
            route_fn = _build_route_condition(stage)

            if route_fn:
                targets = getattr(stage, 'route_targets', [])
                route_map = {t: f'stage_{t}' for t in targets}
                next_stage = stages[i + 1] if i + 1 < len(stages) else None
                default_target = f'stage_{next_stage.name}' if next_stage else '__end__'
                graph.add_conditional_edges(
                    node_id,
                    lambda s: route_map.get(route_fn(s.get('payload', {})), default_target),
                    {**route_map, default_target: default_target},
                )
            elif i + 1 < len(stages):
                graph.add_edge(node_id, f'stage_{stages[i + 1].name}')

        graph.set_entry_point(f'stage_{stages[0].name}')
        app = graph.compile()

        try:
            final_state = app.invoke(HarnessState(**state))
        except Exception as exc:
            self.event_bus.run_failed(workflow_state, error=str(exc))
            return HarnessResult(state=state, completed=False, error=str(exc), failed_stage=state.get('current_stage'))

        self.event_bus.run_completed(workflow_state, completed_stages=final_state.get('completed_stage_ids', []))
        return HarnessResult(state=dict(final_state), completed=not final_state.get('stage_errors'),
                             failed_stage=next(iter(final_state.get('stage_errors', {})), None))
```

### 3.2 BaseStage：增加条件路由

```python
# app/harness/core/stage.py (增强)

class BaseStage:
    name = ''
    description: str = ''
    timeout_ms: int = 30000
    allowed_tools: list[str] = field(default_factory=list)
    requires_policy_check: bool = True
    requires_guardrail: bool = True
    creates_checkpoint_after: bool = False
    risk_level: str = 'low'
    route_targets: list[str] = field(default_factory=list)
    # 需要条件路由的 Stage 实现 route_next(state) → target_name

    def run(self, state: dict, ctx) -> dict:
        raise NotImplementedError

    def route_next(self, state_payload: dict) -> str:
        raise NotImplementedError(f'{self.name} has route_targets but no route_next()')

    def validate_input(self, state: dict, ctx) -> list[str]:
        return []

    def validate_output(self, new_state: dict) -> list[str]:
        return []
```

### 3.3 EventBus 统一

当前 `ExecutionHarness` 内部创建自己的 `EventBus`，`HarnessKernel` 可接受外部 `EventBus`。需要**整个管线共享同一个实例**。

```python
# container.py
from app.harness.core.hooks import EventBus
from app.harness.core.trace_hook import TraceHook, MemoryHook

event_bus = EventBus()
event_bus.register(TraceHook(trace=trace), event='all')
event_bus.register(MemoryHook(memory=task_memory), event='all')
# 同一个 bus 注入所有组件
execution_harness = ExecutionHarness(..., event_bus=event_bus)
harness_kernel = HarnessKernel(event_bus=event_bus)
```

### 3.4 MemoryHook 激活 + Working 清理

当前 `MemoryHook` 存在但零调用——EventBus 统一后自动激活。同时增强清理 working 记忆的能力。

```python
# app/harness/core/trace_hook.py (增强)

class MemoryHook(RuntimeHook):
    def __init__(self, memory: TaskMemory, name: str = 'memory_hook') -> None:
        self._memory = memory
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def handle(self, event: EventPayload) -> None:
        ws = event.workflow_state or {}
        task = ws.get('task')
        if task is None:
            return
        task_id = getattr(task, 'task_id', None)
        if task_id is None:
            return

        step = event.payload.get('step_name') or getattr(task, 'current_step', None)

        # 写事件摘要到 memory_records
        self._memory.append_task_memory(
            task_id, step or 'runtime', 'state',
            f'运行时事件: {event.event.value}',
            payload={'hook_event': event.event.value, 'payload': event.payload},
        )

        # after_stage → 清理 working 记忆
        if event.event in (HookEvent.AFTER_STAGE, HookEvent.STAGE_FAILED, HookEvent.AFTER_REACT_TURN):
            self._clear_working_memory(task_id)

    def _clear_working_memory(self, task_id: str) -> None:
        task = self._memory.get_task(task_id)
        if task is None:
            return
        before = len(task.memory_records)
        task.memory_records = [r for r in task.memory_records if r.scope != 'working']
        if len(task.memory_records) != before:
            self._memory.upsert_task(task)
```

### 3.5 Recipe Stages：填入真实业务逻辑

#### 3.5.1 Query Recipe（6 个 Stage）

```python
# app/harness/recipes/query_recipe.py (重写)

class GuardrailStage(BaseStage):
    name = 'guardrail'
    description = '检查用户输入是否安全'
    requires_guardrail = True

    def run(self, state: dict, ctx) -> dict:
        guardrail = ctx.get('guardrail_engine')
        if guardrail:
            request = state.get('request')
            if request:
                decision = guardrail.validate_request(request)
                state['guardrail_passed'] = decision.allowed
        else:
            state['guardrail_passed'] = True
        return state


class RewriteStage(BaseStage):
    name = 'rewrite'
    description = '对原始查询做改写/扩展'

    def run(self, state: dict, ctx) -> dict:
        runtime = ctx.get('runtime')
        question = state.get('request', {}).get('question', '')
        if runtime and question:
            state['rewritten_query'] = runtime.rewrite_query(question)
        return state


class RetrieveEvidenceStage(BaseStage):
    name = 'retrieve_evidence'
    description = '执行混合检索及图检索'
    allowed_tools = ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence']
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        context_builder = ctx.get('context_harness')
        if context_builder:
            bundle = context_builder.build_context(
                workflow_state=ctx.get('_workflow_state', {}), step_id=self.name)
            state['context_bundle'] = bundle.model_dump()
        if execution:
            try:
                result = execution.run_tool(
                    name='rag_retrieve_evidence',
                    payload={'query': state.get('rewritten_query') or state.get('request', {}).get('question', '')},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['evidence'] = result.model_dump()
            except ToolExecutionError as exc:
                state['evidence_error'] = str(exc)
        return state


class GroundedAnswerStage(BaseStage):
    name = 'grounded_answer'
    description = '结合证据生成回答'
    allowed_tools = ['rag_grounded_answer']

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        if execution and state.get('evidence'):
            try:
                result = execution.run_tool(
                    name='rag_grounded_answer',
                    payload={'question': state.get('request', {}).get('question', ''), 'evidence': state['evidence']},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['answer'] = result.model_dump()
            except ToolExecutionError as exc:
                state['answer_error'] = str(exc)
        return state


class ReflectionStage(BaseStage):
    name = 'self_reflect'
    description = '对生成结果做质量评估'
    creates_checkpoint_after = True
    route_targets = ['retrieve_evidence', 'finalize']

    def run(self, state: dict, ctx) -> dict:
        reflection = ctx.get('reflection_harness')
        if reflection and state.get('answer'):
            decision = reflection.evaluate(state)
            state['reflection_decision'] = decision.model_dump() if hasattr(decision, 'model_dump') else decision
            state['needs_retry'] = getattr(decision, 'needs_retry', False)
        return state

    def route_next(self, state_payload: dict) -> str:
        if state_payload.get('needs_retry'):
            return 'retrieve_evidence'
        return 'finalize'


class FinalizeStage(BaseStage):
    name = 'finalize'
    description = '组装最终响应'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        from app.models.query import QueryResponse
        state['result'] = QueryResponse(
            answer=state.get('answer', {}).get('answer', ''),
            citations=state.get('answer', {}).get('citations', []),
            evidence=state.get('evidence'),
        ).model_dump()
        return state
```

#### 3.5.2 Task Recipe（8 个 Stage）

```python
# app/harness/recipes/task_recipe.py (重写)

class PlanStage(BaseStage):
    name = 'plan'
    description = '根据任务请求生成执行计划'

    def run(self, state: dict, ctx) -> dict:
        planner = ctx.get('planner')
        task = state.get('task')
        if planner and task:
            plan = planner.generate_plan(task)
            state['plan'] = plan.model_dump() if hasattr(plan, 'model_dump') else plan
        return state


class CollectDocumentContextStage(BaseStage):
    name = 'collect_document_context'
    description = '加载文档上下文'
    allowed_tools = ['rag_load_document_context']

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        task = state.get('task', {})
        if execution:
            try:
                result = execution.run_tool(
                    name='rag_load_document_context',
                    payload={'collection_name': task.get('request', {}).get('collection_name', ''), 'doc_ids': task.get('request', {}).get('doc_ids', [])},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['document_context'] = result.model_dump()
            except ToolExecutionError as exc:
                state['context_error'] = str(exc)
        return state


class RetrieveEvidenceStage(BaseStage):
    name = 'retrieve_evidence'
    description = '执行混合及图谱检索'
    allowed_tools = ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence']

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        context_builder = ctx.get('context_harness')
        task = state.get('task', {})
        if context_builder:
            bundle = context_builder.build_context(workflow_state=ctx.get('_workflow_state', {}), step_id=self.name)
            state['context_bundle'] = bundle.model_dump()
        if execution:
            try:
                result = execution.run_tool(
                    name='rag_retrieve_evidence',
                    payload={'query': task.get('request', {}).get('objective', ''), 'collection_name': task.get('request', {}).get('collection_name', '')},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['evidence'] = result.model_dump()
            except ToolExecutionError as exc:
                state['evidence_error'] = str(exc)
        return state


class AnalyzeStage(BaseStage):
    name = 'analyze'
    description = '基于证据执行结构化分析'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        prompt_builder = ctx.get('prompt_builder')
        grounding = ctx.get('grounding_engine')
        evidence = state.get('evidence')
        llm = ctx.get('llm')
        if prompt_builder and evidence:
            prompt = prompt_builder.render('analyze', {'evidence': evidence})
            if llm and prompt:
                state['analysis'] = llm.complete(prompt.text).text
                if grounding:
                    state['grounding'] = grounding.align_claims(state['analysis'], evidence).model_dump()
        return state


class DraftReportStage(BaseStage):
    name = 'draft_report'
    description = '生成报告草稿'
    allowed_tools = ['draft_report']
    risk_level = 'medium'

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        if execution and state.get('analysis'):
            try:
                result = execution.run_tool(
                    name='draft_report',
                    payload={'analysis': state['analysis'], 'evidence': state.get('evidence')},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['draft'] = result.model_dump()
            except ToolExecutionError as exc:
                state['draft_error'] = str(exc)
        return state


class ReviewStage(BaseStage):
    name = 'review'
    description = '对报告草稿做质量审查'
    allowed_tools = ['review_report']
    route_targets = ['revise', 'finalize_report']

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        if execution and state.get('draft'):
            try:
                result = execution.run_tool(
                    name='review_report', payload={'draft': state['draft']},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['review'] = result.model_dump()
                state['review_passed'] = result.get('passed', False)
            except ToolExecutionError as exc:
                state['review_error'] = str(exc)
        return state

    def route_next(self, state_payload: dict) -> str:
        if not state_payload.get('review_passed', True):
            return 'revise'
        return 'finalize_report'


class ReviseStage(BaseStage):
    name = 'revise'
    description = '根据审查意见修订报告'
    allowed_tools = ['draft_report']

    def run(self, state: dict, ctx) -> dict:
        if not state.get('review_passed', True):
            execution = ctx.get('execution_harness')
            if execution and state.get('draft') and state.get('review'):
                try:
                    result = execution.run_tool(
                        name='draft_report',
                        payload={'draft': state['draft'], 'review_feedback': state['review']},
                        workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                    )
                    state['draft'] = result.model_dump()
                    state['revised'] = True
                except ToolExecutionError as exc:
                    state['revise_error'] = str(exc)
        return state


class FinalizeReportStage(BaseStage):
    name = 'finalize_report'
    description = '最终确认并输出报告'
    allowed_tools = ['finalize_report']
    risk_level = 'high'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        if execution and state.get('draft'):
            try:
                result = execution.run_tool(
                    name='finalize_report', payload={'draft': state['draft']},
                    workflow_state=ctx.get('_workflow_state', {}), context_bundle=None,
                )
                state['final_artifact'] = result.model_dump()
            except ToolExecutionError as exc:
                state['finalize_error'] = str(exc)
        return state
```

### 3.6 SessionManager 重构

将 `SessionManager` 从纯内存改为 `InMemoryState` + `SQLiteStateStore` 双写，并在消息交互时写入 `MemoryRecord(scope='session')`。

```python
# app/services/session_manager.py (重构)

class SessionManager:
    def __init__(
        self,
        state: InMemoryState,
        persistence: SQLiteStateStore,
        task_memory: TaskMemory | None = None,
        max_history: int = 100,
    ) -> None:
        self._state = state
        self._persistence = persistence
        self._task_memory = task_memory
        self._max_history = max_history

    async def get_or_create(self, session_id: str | None = None) -> Session:
        """优先从 state → SQLite → 新建。"""
        if session_id:
            raw = self._state.sessions.get(session_id)
            if raw:
                return Session(**raw)
            raw = self._persistence.get_session(session_id)
            if raw:
                self._state.sessions[session_id] = raw
                return Session(**raw)
        session = Session(id=session_id or str(uuid4()))
        await self.save(session)
        return session

    async def save(self, session: Session) -> None:
        """双写：InMemoryState + SQLiteStateStore。"""
        session.updated_at = datetime.now(timezone.utc)
        payload = session.model_dump(mode='json')
        self._state.sessions[session.id] = payload
        self._persistence.upsert_session(session.id, payload)

    async def add_message(self, session_id: str, message: Message) -> None:
        """追加消息 → 持久化 + 写 MemoryRecord(scope='session')。"""
        session = await self.get_or_create(session_id)
        session.history.append(message)
        if len(session.history) > self._max_history:
            session.history = session.history[-self._max_history:]
        await self.save(session)

        # 同步写记忆总线
        if self._task_memory:
            self._task_memory.append_memory_record(MemoryRecord(
                memory_id=f'ses-{uuid4().hex[:12]}',
                scope='session', kind='observation', trust_level='verified',
                source='user' if message.role == 'user' else 'system',
                summary=message.content[:200],
                payload={'session_id': session.id, 'role': message.role, 'content_preview': message.content[:500]},
                created_at=message.timestamp or datetime.now(timezone.utc),
            ))
```

### 3.7 UserProfileService（新增）

```python
# app/services/user_profile_service.py (新增)

class UserProfile(BaseModel):
    user_id: str
    preferences: dict[str, Any] = Field(default_factory=lambda: {
        'language': 'zh', 'output_format': 'markdown',
        'risk_tolerance': 'medium', 'tools_disabled': [],
        'temperature': 0.7, 'max_tool_calls': 20,
    })
    behavioral_traits: dict[str, Any] = Field(default_factory=dict)
    interaction_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserProfileService:
    def __init__(self, persistence: SQLiteStateStore, task_memory: TaskMemory | None = None):
        self._persistence = persistence
        self._task_memory = task_memory

    def get_or_create(self, user_id: str) -> UserProfile:
        raw = self._persistence.get_user_profile(user_id)
        if raw:
            return UserProfile(**raw)
        profile = UserProfile(user_id=user_id)
        self.upsert_profile(profile)
        return profile

    def upsert_profile(self, profile: UserProfile) -> None:
        profile.updated_at = datetime.now(timezone.utc)
        self._persistence.upsert_user_profile(profile.user_id, profile.model_dump(mode='json'))
        if self._task_memory:
            self._task_memory.append_memory_record(MemoryRecord(
                memory_id=f'profile-{profile.user_id}',
                scope='profile', kind='preference', trust_level='verified', source='system',
                summary=f'User {profile.user_id} profile updated',
                payload=profile.preferences, created_at=datetime.now(timezone.utc),
            ))

    def infer_and_update(self, user_id: str, interaction: dict[str, Any]) -> UserProfile:
        """从交互推断偏好。"""
        profile = self.get_or_create(user_id)
        profile.interaction_count += 1
        message = interaction.get('message', '')
        if message:
            import re
            if re.search(r'[\u4e00-\u9fff]', message):
                profile.preferences['language'] = 'zh'
            elif message and message[0].isascii() and message[0].isalpha():
                profile.preferences['language'] = 'en'
        tool_calls = interaction.get('tool_calls', [])
        if tool_calls:
            high_risk = sum(1 for t in tool_calls if t.get('risk_level') == 'high')
            profile.preferences['risk_tolerance'] = 'high' if high_risk > 3 else ('low' if high_risk == 0 else 'medium')
        traits = profile.behavioral_traits
        traits['avg_tool_calls_per_session'] = traits.get('avg_tool_calls_per_session', 0) * 0.7 + len(tool_calls) * 0.3
        traits['last_mode'] = interaction.get('mode', 'chat')
        self.upsert_profile(profile)
        return profile
```

### 3.8 MemoryCommitGate（新增）

```python
# app/services/memory_commit_gate.py (新增)

class MemoryCommitGate:
    """记忆提交门：信任提升 + scope 晋升 + 冲突检测。"""

    def __init__(self, task_memory: TaskMemory):
        self._task_memory = task_memory

    TRUST_LEVELS = ['unverified', 'provisional', 'verified', 'final']

    def promote_trust(self, task_id: str, memory_id: str) -> MemoryRecord | None:
        """单条记忆提升一个信任等级。"""
        records = self._task_memory.query_memory_records(task_id)
        for r in records:
            if r.memory_id == memory_id:
                idx = self.TRUST_LEVELS.index(r.trust_level)
                if idx < len(self.TRUST_LEVELS) - 1:
                    r.trust_level = self.TRUST_LEVELS[idx + 1]  # type: ignore
                    self._task_memory.append_memory_record(r)
                    return r
        return None

    def auto_promote(self, task_id: str) -> int:
        """自动提升：3+ 引用 → verified，24h 无冲突 → final。"""
        records = self._task_memory.query_memory_records(task_id)
        count = 0
        for r in records:
            if r.trust_level == 'provisional':
                refs = sum(1 for x in records if r.memory_id in x.conflict_refs or x.summary == r.summary)
                if refs >= 3:
                    r.trust_level = 'verified'
                    self._task_memory.append_memory_record(r)
                    count += 1
            elif r.trust_level == 'verified':
                age = datetime.now(timezone.utc) - r.created_at
                if age > timedelta(hours=24) and not r.conflict_refs:
                    r.trust_level = 'final'
                    self._task_memory.append_memory_record(r)
                    count += 1
        return count

    def commit_to_semantic(self, task_id: str) -> list[MemoryRecord]:
        """任务完成后将高信任 run 记录晋升为 semantic。"""
        task = self._task_memory.get_task(task_id)
        if not task or task.status != 'completed':
            return []
        promoted, seen = [], set()
        for r in task.memory_records:
            if r.scope != 'run' or r.trust_level not in ('verified', 'final') or r.summary in seen:
                continue
            seen.add(r.summary)
            semantic = MemoryRecord(
                memory_id=f'sem-{uuid4().hex[:12]}', scope='semantic', kind=r.kind,
                trust_level=r.trust_level, source=r.source, summary=r.summary,
                payload={**r.payload, 'origin_task_id': task_id, 'origin_run_id': r.related_task_run_id},
                related_task_run_id=task_id, created_at=datetime.now(timezone.utc),
            )
            self._task_memory.append_memory_record(semantic)
            promoted.append(semantic)
        return promoted

    def resolve_conflicts(self, task_id: str) -> int:
        records = self._task_memory.query_memory_records(task_id)
        count = 0
        for i, a in enumerate(records):
            for b in records[i + 1:]:
                if a.kind == b.kind and a.scope == b.scope and a.summary != b.summary and a.summary and b.summary:
                    if b.memory_id not in a.conflict_refs:
                        a.conflict_refs.append(b.memory_id)
                        b.conflict_refs.append(a.memory_id)
                        count += 1
        return count

    def mark_stale(self, task_id: str, new_plan_id: str) -> int:
        records = self._task_memory.query_memory_records(task_id)
        count = 0
        for r in records:
            if r.checkpoint_ref and r.checkpoint_ref != new_plan_id and not r.stale:
                r.stale = True
                count += 1
        return count
```

### 3.9 ToolContext 增加 memory 引用

```python
# app/agents/tools/base.py (增强)

@dataclass
class ToolContext:
    # ... 现有字段不变 ...
    memory: TaskMemory | None = None  # ★ 新增
```

注入链：`container.py → Orchestrator → ExecutionHarness → ToolExecutor._tool_context() → ToolContext.memory`

### 3.10 Semantic 记忆注入 ContextBundle

```python
# app/harness/components/context_builders.py (增强)

class TaskContextBuilder:
    def build(self, ...) -> ContextBundle:
        # ... 现有逻辑 ...

        # ★ 新增：检索 semantic 记忆
        semantic_memory = []
        if self._task_memory:
            semantic_memory = self._task_memory.query_memory_records(
                scope='semantic', trust_level='verified', limit=20,
            )
        bundle.memory_slice['semantic'] = [{'summary': r.summary, 'kind': r.kind} for r in semantic_memory]
        if semantic_memory:
            bundle.source_summary['semantic_memory'] = f'semantic_memory/{len(semantic_memory)}_records'

        return bundle
```

### 3.11 Orchestrator 改造（含记忆融合点）

#### 3.11.1 QueryWorkflowOrchestrator

```python
class QueryWorkflowOrchestrator:
    def __init__(self, ...):
        # ... 现有初始化 ...
        self.recipe_registry = build_default_recipe_registry()
        self.harness_kernel = HarnessKernel(event_bus=execution_harness.event_bus)

    def query(self, request: QueryRequest) -> QueryResponse:
        recipe = self.recipe_registry.get_by_task_type('query')
        task_spec = build_query_task_spec(request, mode='query')
        state = {'payload': {'request': request.model_dump(), 'task_spec': task_spec.model_dump(), 'mode': 'query'}}
        ctx = {
            'execution_harness': self.execution_harness,
            'context_harness': self.context_harness,
            'guardrail_engine': self.guardrail_engine,
            'reflection_harness': self.reflection_harness,
            'runtime': self._runtime, 'llm': self._llm, 'task_memory': self._memory,
            '_workflow_state': {'task': task_spec.model_dump()},
        }

        result = self.harness_kernel.run(recipe, state, ctx, workflow_state={'task': task_spec.model_dump()})
        if not result.completed:
            return QueryResponse(answer=f'Failed at stage: {result.failed_stage}', error=result.error)

        final = result.state.get('payload', {})
        return QueryResponse(
            answer=final.get('answer', {}).get('answer', ''),
            citations=final.get('answer', {}).get('citations', []),
            evidence=final.get('evidence'),
        )
```

#### 3.11.2 TaskWorkflowOrchestrator（记忆融合点）

```python
class TaskWorkflowOrchestrator:
    def __init__(self, ...):
        # ... 现有初始化 ...
        self.recipe_registry = build_default_recipe_registry()
        self.harness_kernel = HarnessKernel(event_bus=execution_harness.event_bus)
        # ★ 记忆融合点：MemoryCommitGate
        self.memory_gate = MemoryCommitGate(task_memory)
        self.user_profile_service = UserProfileService(persistence, task_memory)

    def run(self, task: TaskDetail) -> TaskResult:
        recipe = self.recipe_registry.get_by_task_type(task.request.task_type)
        state = {'payload': {'task': task.model_dump()}}
        ctx = {
            'execution_harness': self.execution_harness,
            'context_harness': self.context_harness,
            'evaluation_harness': self.evaluation_harness,
            'guardrail_engine': self.guardrail_engine,
            'policy_engine': self.policy_engine,
            'prompt_builder': self._prompt_builder,
            'grounding_engine': self._grounding_engine,
            'planner': self.planner, 'llm': self._llm, 'task_memory': self._memory,
        }

        result = self.harness_kernel.run(recipe, state, ctx, workflow_state={'task': task.model_dump()})
        if not result.completed:
            return TaskResult(task_id=task.task_id, status='failed', error=f'Failed at: {result.failed_stage}')

        final = result.state.get('payload', {})

        # ★ 记忆融合点：任务完成后晋升 semantic
        self.memory_gate.commit_to_semantic(task.task_id)
        self.memory_gate.resolve_conflicts(task.task_id)

        return TaskResult(
            task_id=task.task_id, status='completed',
            final_artifact=final.get('final_artifact'),
        )
```

---

## 4. SQLite 存储变更

```sql
-- 用户画像表（新增）
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT
);

-- 语义/长期记忆独立存储（新增）
CREATE TABLE IF NOT EXISTS semantic_memory (
    memory_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'semantic',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_semantic_memory_scope ON semantic_memory(scope);

-- sessions 表（已有，需验证 get_session / upsert_session / delete_session 完备）
```

`SQLiteStateStore` 新增方法：
- `get_user_profile(user_id)`, `upsert_user_profile(user_id, payload)`, `delete_user_profile(user_id)`
- `get_semantic_memory(memory_id)`, `query_semantic_memory(limit, scope)`, `upsert_semantic_memory(memory_id, payload)`

---

## 5. 配置变更

```python
# app/core/config.py — Settings 新增

# 记忆系统
memory_max_records_per_task: int = Field(default=200, alias='MEMORY_MAX_RECORDS_PER_TASK')
memory_enable_semantic: bool = Field(default=True, alias='MEMORY_ENABLE_SEMANTIC')
memory_enable_profile: bool = Field(default=True, alias='MEMORY_ENABLE_PROFILE')
memory_auto_promote_interval_minutes: int = Field(default=60, alias='MEMORY_AUTO_PROMOTE_INTERVAL_MINUTES')
session_max_history: int = Field(default=100, alias='SESSION_MAX_HISTORY')
```

---

## 6. 完整文件变更清单

| 文件 | 动作 | 归属 | 说明 |
|------|------|------|------|
| `app/harness/core/kernel.py` | **重写** | Harness | 顺序 iterator → LangGraph graph builder |
| `app/harness/core/stage.py` | 增强 | Harness | 增加 `route_next()` / `route_targets` |
| `app/harness/core/hooks.py` | 无需改 | Harness | EventBus 已支持全生命周期 |
| `app/harness/core/trace_hook.py` | **增强** | **兼** | MemoryHook 激活 + working 清理 |
| `app/harness/core/runtime_context.py` | **删除** | Harness | 被 ctx dict 替代 |
| `app/harness/core/sandbox_extensions.py` | **删除** | Harness | 零调用，被 ToolSandbox 覆盖 |
| `app/harness/recipes/query_recipe.py` | **重写** | Harness | 6 个 Stage 填真实逻辑 + route_next() |
| `app/harness/recipes/task_recipe.py` | **重写** | Harness | 8 个 Stage 填真实逻辑 + route_next() |
| `app/harness/execution.py` | 微调 | Harness | 接受外部 event_bus |
| `app/harness/components/context_builders.py` | 增强 | Memory | semantic 注入 ContextBundle |
| `app/services/session_manager.py` | **重构** | Memory | 接入 state + persistence + task_memory |
| `app/services/user_profile_service.py` | **新增** | Memory | UserProfile 模型 + CRUD + 偏好推断 |
| `app/services/memory_commit_gate.py` | **新增** | Memory | 信任提升 + scope 晋升 + 冲突检测 |
| `app/services/sqlite_store.py` | 增强 | Memory | user_profiles / semantic_memory 表 |
| `app/agents/tools/base.py` | 增强 | Memory | ToolContext 增加 memory 字段 |
| `app/agents/memory.py` | 无需改 | Memory | 现有 API 已足够 |
| `app/workflows/query_orchestrator.py` | **改造** | Harness | 调 kernel.run() |
| `app/workflows/tasks/task_orchestrator.py` | **改造** | **兼** | kernel.run() + commit_to_semantic() |
| `app/workflows/query_graph.py` | **删除** | Harness | 不再需要手动建图 |
| `app/workflows/query_nodes.py` | **删除** | Harness | 逻辑已迁移到 Stage |
| `app/workflows/tasks/document_analysis_graph.py` | **删除** | Harness | 不再需要手动建图 |
| `app/workflows/tasks/document_analysis_nodes.py` | **删除** | Harness | 逻辑已迁移到 Stage |
| `app/container.py` | 修改 | **兼** | 共享 EventBus + 装配新组件 |
| `app/core/config.py` | 新增 | Memory | 记忆系统配置项 |

---

## 7. 实施计划

### Phase 1 — 基础设施（1 天，Harness）

| 任务 | 文件 | 说明 |
|------|------|------|
| HarnessKernel 重写 | `core/kernel.py` | LangGraph graph builder |
| BaseStage 增强 | `core/stage.py` | route_next() / route_targets |
| EventBus 统一 | `container.py` + `execution.py` | 共享 EventBus 实例 |
| MemoryHook 激活 | `trace_hook.py` | 注册到 EventBus + working 清理 |

### Phase 2 — Session 持久化（1 天，Memory）

| 任务 | 文件 | 说明 |
|------|------|------|
| SessionManager 重构 | `session_manager.py` | state + persistence + task_memory |
| SQLite 补充 | `sqlite_store.py` | 确保 get_session/upsert_session 完备 |
| 容器装配 | `container.py` | 传递依赖到 SessionManager |

### Phase 3 — Query Recipe 迁移（1.5 天，Harness）

| 任务 | 文件 | 说明 |
|------|------|------|
| 6 个 Stage 填逻辑 | `recipes/query_recipe.py` | guardrail → finalize |
| Orchestrator 切换 | `query_orchestrator.py` | kernel.run() |
| 删除旧文件 | `query_graph.py`, `query_nodes.py` | |

### Phase 4 — Task Recipe 迁移（1.5 天，Harness + Memory 融合）

| 任务 | 文件 | 说明 |
|------|------|------|
| 8 个 Stage 填逻辑 | `recipes/task_recipe.py` | plan → finalize_report |
| Orchestrator 切换 | `task_orchestrator.py` | kernel.run() |
| 记忆融合 | `task_orchestrator.py` | commit_to_semantic() ★ |
| 删除旧文件 | `document_analysis_graph.py`, `document_analysis_nodes.py` | |

### Phase 5 — 记忆加工（1 天，Memory）

| 任务 | 文件 | 说明 |
|------|------|------|
| MemoryCommitGate | `memory_commit_gate.py`（新） | 信任提升 + scope 晋升 + 冲突检测 |
| ToolContext.memory | `base.py` | 增加 memory 字段 + 注入链 |
| Semantic 注入 ContextBundle | `context_builders.py` | 检索 semantic 记录 |

### Phase 6 — 用户画像 + 清理（1 天，Memory）

| 任务 | 文件 | 说明 |
|------|------|------|
| UserProfileService | `user_profile_service.py`（新） | 模型 + CRUD + 偏好推断 |
| user_profiles 表 | `sqlite_store.py` | 建表 + 读写方法 |
| 删除死代码 | `runtime_context.py`, `sandbox_extensions.py` | |
| 更新架构文档 | `架构.md` | 反映新架构 |
| 运行全部测试 | — | 确保不出现回归 |

---

## 8. 融合点总结

| 融合点 | 涉及文件 | 说明 |
|--------|---------|------|
| **EventBus → MemoryHook** | `trace_hook.py`, `container.py` | Harness 统一 EventBus 后 MemoryHook 自动激活 |
| **Stage after_stage → working 清理** | `trace_hook.py` | MemoryHook 监听 AFTER_STAGE 事件清理 |
| **task_orchestrator → commit_to_semantic()** | `task_orchestrator.py` | Harness 改造后的 orchestrator 收尾处调 MemoryCommitGate |
| **ToolContext.memory 注入链** | `base.py`, `tool_executor.py` | 依赖 Harness 的 ExecutionRuntimeDependencies 链 |
| **ContextBundle → semantic 注入** | `context_builders.py` | ContextHarness 构建时自动检索 semantic 记忆 |

---

## 9. 设计原则

### 原则 1：MemoryRecord 是唯一格式
所有记忆——会话消息、工具调用、用户画像、跨任务知识——统一用 `MemoryRecord`，scope 区分层次。

### 原则 2：记忆总线只有一个入口
所有写操作通过 `TaskMemory.append_memory_record()`，不绕过统一入口。

### 原则 3：晋升需要门控
`run → semantic` 必须通过 `MemoryCommitGate`，确保只有高信任度的知识进入长期记忆。

### 原则 4：扩展靠继承，执行靠组合
- `BaseRecipe` / `BaseStage` 通过继承扩展
- `HarnessKernel` + `LangGraph` 通过组合执行

### 原则 5：治理不散落在 Stage 中
Stage 只发射事件 → EventBus → HookRegistry → TraceHook/MemoryHook，不在 Stage 内直接调 trace/memory。

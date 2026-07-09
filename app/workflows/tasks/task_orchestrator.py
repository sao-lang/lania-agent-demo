"""任务工作流编排器模块。

负责为文档分析类任务准备运行依赖，并把领域节点接入统一 route runtime。该模块本身不
实现具体节点逻辑，主要承担“组装和触发任务图”的职责。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, cast
from uuid import uuid4

from app.agents.memory import TaskMemory
from app.agents.planner import TaskPlanner
from app.agents.subagents import ContractAgent, EvidenceAgent, ReportingAgent, ReviewAgent, SubAgentRegistry, SubAgentRuntime
from app.agents.tools.registry import ToolRegistry
from app.core.config import Settings
from app.harness.context import ContextHarness
from app.harness.evaluation import EvaluationHarness
from app.harness.execution import ExecutionHarness
from app.harness.hooks import EventBus
from app.services.memory_commit_gate import MemoryCommitGate
from app.harness.guardrails import GuardrailEngine
from app.harness.model_router import ModelRouter
from app.harness.grounding import GroundingBundle
from app.harness.models import ContextBundle
from app.models.runtime_contracts import ResultContract
from app.harness.policy import PolicyEngine
from app.harness.react_runtime import BoundedLocalReActRuntime
from app.models.artifact import EvidencePack, ReportArtifactContent, ReviewResult, RiskItem
from app.models.task import CheckpointRecord, StepRuntimeRecord, TaskDetail, TaskPlan, TaskResult, TaskRunDetail, TaskRunEvent, TaskRunSummary
from app.rag.facade import RagFacade
from app.rag.observability import TraceRecorder
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.workflows.tasks.document_analysis_graph import build_document_analysis_graph
from app.workflows.tasks.document_analysis_nodes import DocumentAnalysisNodes
from app.workflows.tasks.skill import TaskSkill, TaskSkillRegistry, build_default_task_skill_registry


class TaskWorkflowOrchestrator:
    """任务工作流编排器主类。

    负责为文档分析类任务准备运行依赖、解析领域 skill、调用 LangGraph task app，并在执行结束后
    统一处理持久化、checkpoint replay / resume 和错误收口。
    """

    def __init__(
        self,
        planner: TaskPlanner,
        registry: ToolRegistry,
        memory: TaskMemory,
        trace: TraceRecorder,
        settings: Settings,
        state: InMemoryState,
        retrieval,
        vector_store,
        llm,
        subagent_runtime: SubAgentRuntime | None = None,
        context_harness: ContextHarness | None = None,
        execution_harness: ExecutionHarness | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        evaluation_harness: EvaluationHarness | None = None,
        react_runtime: BoundedLocalReActRuntime | None = None,
        capabilities: dict[str, Any] | None = None,
        persistence: SQLiteStateStore | None = None,
        skill_registry: TaskSkillRegistry | None = None,
        model_router: ModelRouter | None = None,
        services: dict[str, Any] | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """初始化任务工作流编排器。

        Args:
            planner: 任务计划生成器。
            registry: 工具注册表。
            memory: 任务内存与持久化访问封装。
            trace: 链路追踪记录器。
            settings: 全局配置对象。
            state: 内存态业务数据。
            retrieval: 检索服务实例。
            vector_store: 向量库访问封装。
            llm: 可选大模型实例。
            subagent_runtime: 可选子 Agent 运行时；为空时自动创建默认实现。
        """
        self.planner = planner
        self.registry = registry
        self.memory = memory
        self.trace = trace
        self.settings = settings
        self.state = state
        self.retrieval = retrieval
        self.vector_store = vector_store
        self.llm = llm
        self.model_router = model_router or ModelRouter()
        caps = capabilities or {}
        self.knowledge_capability = caps.get('knowledge')
        self.rag_facade = caps.get('rag')
        if self.rag_facade is None and self.knowledge_capability is not None:
            self.rag_facade = RagFacade(self.knowledge_capability)
        self.capabilities = caps
        if subagent_runtime is None:
            # 任务系统允许独立构建运行时，避免容器外测试时必须显式注入子 Agent。
            registry_for_runtime = SubAgentRegistry()
            registry_for_runtime.register(EvidenceAgent(memory, trace))
            registry_for_runtime.register(ReportingAgent(memory, trace))
            registry_for_runtime.register(ReviewAgent(memory, trace))
            registry_for_runtime.register(ContractAgent(memory, trace))
            subagent_runtime = SubAgentRuntime(registry_for_runtime, trace)
        self.subagent_runtime = subagent_runtime
        self.guardrail_engine = guardrail_engine or GuardrailEngine(registry)
        self.policy_engine = policy_engine or PolicyEngine(settings)
        self.context_harness = context_harness or ContextHarness(memory, registry, settings)
        self.evaluation_harness = evaluation_harness or EvaluationHarness(memory, trace, settings, self.policy_engine)
        self.react_runtime = react_runtime or BoundedLocalReActRuntime()
        self.services = services or {}
        self.execution_harness = execution_harness or ExecutionHarness(
            registry,
            memory,
            trace,
            settings,
            state,
            retrieval,
            vector_store,
            llm,
            capabilities=self.capabilities,
            guardrail_engine=self.guardrail_engine,
            policy_engine=self.policy_engine,
            model_router=self.model_router,
            services=self.services,
            event_bus=event_bus,
        )
        self.persistence = persistence
        self._nodes: DocumentAnalysisNodes | None = None
        self._task_app: Any | None = None
        self.skill_registry = skill_registry or build_default_task_skill_registry()
        # ★ 记忆融合点：MemoryCommitGate
        self.memory_gate = MemoryCommitGate(memory)

    def run(self, task: TaskDetail) -> TaskResult:
        """执行任务工作流。

        Args:
            task: 待执行的任务详情对象。

        Returns:
            任务执行结果。

        Raises:
            RuntimeError: 当工作流结束后未生成 `TaskResult` 时抛出。
        """

        skill = self._resolve_skill(task.request.task_type)
        state = skill.build_initial_state(task)
        result_state = self._invoke_workflow(state)
        result = result_state.get('result')
        if result is None:
            raise RuntimeError('task workflow finished without TaskResult')
        return result

    def list_task_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        collection_name: str | None = None,
        recoverable_only: bool = False,
    ) -> list[TaskRunSummary]:
        """列出 task runtime 历史。

        该接口主要面向任务运行态观测与恢复入口展示，因此支持按状态、集合名和是否可恢复做过滤。
        """
        return self.memory.list_task_runs(
            limit=limit,
            offset=offset,
            status=status,
            collection_name=collection_name,
            recoverable_only=recoverable_only,
        )

    def get_task_run(self, run_id: str) -> TaskRunDetail | None:
        """读取单个 task runtime 详情。"""
        return self.memory.get_task_run(run_id)

    def replay_task_run(self, run_id: str, checkpoint_id: str | None = None) -> TaskRunDetail:
        """从指定 task runtime checkpoint 发起重放。

        replay 会从 checkpoint 快照恢复一份新的 workflow state，并以 checkpoint 记录的
        `next_route` 作为图入口继续执行。
        """
        detail = self.get_task_run(run_id)
        if detail is None:
            raise LookupError(run_id)
        checkpoint = self._resolve_checkpoint(detail, checkpoint_id)
        state = self._state_from_checkpoint(detail, checkpoint)
        state['replayed_from_checkpoint_id'] = checkpoint.checkpoint_id
        result_state = self._invoke_workflow(state, initial_route=checkpoint.next_route)
        self._persist_task_run(result_state['task'])
        latest = self.get_task_run(result_state['task'].task_run.run_id if result_state['task'].task_run is not None else run_id)
        if latest is None:
            raise RuntimeError('task replay finished without persisted run detail')
        return latest

    def resume_task_run(self, run_id: str) -> TaskRunDetail:
        """从最近 checkpoint 恢复 task runtime。

        与 replay 的区别只在 checkpoint 选择策略：resume 固定取最后一个 checkpoint，作为用户
        语义上的“继续执行”。
        """
        detail = self.get_task_run(run_id)
        if detail is None:
            raise LookupError(run_id)
        if not detail.recoverable or not detail.checkpoints:
            raise RuntimeError('task run is not recoverable')
        return self.replay_task_run(run_id, checkpoint_id=detail.checkpoints[-1].checkpoint_id)

    def _get_nodes(self) -> DocumentAnalysisNodes:
        """懒加载节点集合。

        节点对象内部持有 planner、memory、harness 和 policy 等依赖，创建成本不低，因此在
        orchestrator 生命周期内复用同一份实例。
        """
        if self._nodes is None:
            self._nodes = DocumentAnalysisNodes(
                self.planner,
                self.registry,
                self.memory,
                self.trace,
                self.settings,
                self.state,
                self.retrieval,
                self.vector_store,
                self.llm,
                self.subagent_runtime,
                self.context_harness,
                self.execution_harness,
                self.guardrail_engine,
                self.policy_engine,
                self.evaluation_harness,
            )
        return self._nodes

    def _resolve_skill(self, task_type: str) -> TaskSkill:
        """按 `task_type` 解析领域 skill。"""
        try:
            return self.skill_registry.get(task_type)
        except KeyError as exc:
            raise RuntimeError(f'unsupported task skill: {task_type}') from exc

    def _invoke_workflow(
        self,
        state: dict[str, Any],
        *,
        initial_route: str = 'load_task',
    ) -> dict[str, Any]:
        """以 LangGraph compiled app 执行 task workflow。

        `initial_route` 允许工作流从 checkpoint 指定的节点重新进入，是 replay/resume 能力的
        基础。
        """
        # 通过 graph_entry_route 把首次执行与 checkpoint 恢复统一到同一条调用路径。
        runtime_state = {**state, 'graph_entry_route': initial_route}
        result = self._get_task_app().invoke(runtime_state)
        result_state = cast(dict[str, Any], result)
        result_state['graph_entry_route'] = None
        self._finalize_successful_task_state(result_state)
        return result_state

    # ── 记忆融合点 ────────────────────────────────────────────────────────────

    def _commit_memory(self, task_id: str) -> None:
        """任务完成后自动晋升 run → semantic 记忆。

        该方法是记忆融合的核心入口，在任务成功完成后调用。
        """
        try:
            self.memory_gate.auto_promote(task_id)
            promoted = self.memory_gate.commit_to_semantic(task_id)
            self.memory_gate.resolve_conflicts(task_id)
            if promoted:
                self.trace.record(
                    'memory_commit_completed',
                    {
                        'task_id': task_id,
                        'promoted_count': len(promoted),
                    },
                )
        except Exception:
            pass  # 记忆提交失败不影响主流程

    # ── 图构建与恢复 ──────────────────────────────────────────────────────────

    def _get_task_app(self) -> Any:
        """懒加载文档分析 workflow compiled graph。

        编译图对象通常可被同一个 orchestrator 实例重复复用，因此这里做一次性构建，避免每次
        运行任务都重新声明节点和边。
        """
        if self._task_app is None:
            self._task_app = build_document_analysis_graph(
                self.planner,
                self.registry,
                self.memory,
                self.trace,
                self.settings,
                self.state,
                self.retrieval,
                self.vector_store,
                self.llm,
                self.subagent_runtime,
                self.context_harness,
                self.execution_harness,
                self.guardrail_engine,
                self.policy_engine,
                self.evaluation_harness,
                checkpoint_after_step=self._checkpoint_after_step,
                on_node_error=lambda runtime_state, route, exc: self._handle_task_runtime_error(
                    self._get_nodes(),
                    runtime_state,
                    route,
                    exc,
                ),
            )
        return self._task_app

    def _handle_task_runtime_error(
        self,
        nodes: DocumentAnalysisNodes,
        state: dict[str, Any],
        route: str,
        exc: Exception,
    ) -> None:
        """统一处理 task runtime route 失败。

        图节点抛错后，这里负责把失败写回 `TaskDetail`、同步 context bundle / run events，并立即
        持久化，保证恢复逻辑能拿到尽可能新的失败现场。
        """
        latest_task = cast(TaskDetail, state['task'])
        latest_task = nodes.mark_step_failed(latest_task, route, str(exc))
        latest_task.context_bundles = {
            key: value
            for key, value in cast(dict[str, ContextBundle], state.get('context_bundles') or {}).items()
        }
        latest_task.run_events = [TaskRunEvent.model_validate(item) for item in state.get('run_events') or []]
        state['task'] = latest_task
        self._persist_task_run(latest_task)

    def _finalize_successful_task_state(self, state: dict[str, Any]) -> None:
        """收口 task runtime 完成态。

        任务图执行结束后，仍需要由 orchestrator 统一补齐 `TaskRun` 完成态、最终结果契约和
        `workflow_completed` 事件，确保持久化记录形成闭环。
        """
        latest_task = cast(TaskDetail, state['task'])
        latest_task.ensure_runtime_contracts()
        if latest_task.task_run is not None:
            latest_task.task_run.status = 'completed'
            latest_task.task_run.completed_at = datetime.now(timezone.utc)
            latest_task.task_run.current_step_id = cast(Optional[str], state.get('current_step_id'))
            latest_task.task_run.completed_step_ids = list(state.get('completed_step_ids') or [])
            latest_task.task_run.step_runtimes = {
                key: value
                for key, value in cast(dict[str, StepRuntimeRecord], state.get('step_runtimes') or {}).items()
            }
            latest_task.task_run.checkpoints = list(cast(list[CheckpointRecord], state.get('checkpoints') or []))
        latest_task.context_bundles = {
            key: value
            for key, value in cast(dict[str, ContextBundle], state.get('context_bundles') or {}).items()
        }
        latest_task.run_events = [TaskRunEvent.model_validate(item) for item in state.get('run_events') or []]
        final_artifact = latest_task.final_artifact
        latest_task.result_contract = ResultContract(
            kind=cast(Optional[str], state.get('artifact_type')) or 'document_analysis_report',
            skill_name=cast(Optional[str], state.get('skill_name')) or latest_task.request.task_type,
            final_artifact_id=latest_task.final_artifact_id,
            artifact_type=final_artifact.artifact_type if final_artifact is not None else cast(Optional[str], state.get('artifact_type')) or 'document_analysis_report',
            artifact_status=final_artifact.status if final_artifact is not None else 'final',
            artifact_version=final_artifact.version if final_artifact is not None else None,
            artifact_ids=list(latest_task.artifact_ids),
            replayed_from_checkpoint_id=cast(Optional[str], state.get('replayed_from_checkpoint_id')),
        )
        if not any(item.name == 'workflow_completed' for item in latest_task.run_events):
            # 某些节点只负责产出领域结果，不直接写 workflow 级完成事件，这里统一兜底补齐。
            latest_task.run_events.append(
                TaskRunEvent(
                    event_id=f'revt-{uuid4().hex[:12]}',
                    name='workflow_completed',
                    timestamp=latest_task.task_run.completed_at if latest_task.task_run is not None else datetime.now(timezone.utc),
                    payload={
                        'workflow': cast(Optional[str], state.get('skill_name')) or latest_task.request.task_type,
                        'skill_name': cast(Optional[str], state.get('skill_name')) or latest_task.request.task_type,
                        'status': 'ok',
                        'task_type': latest_task.request.task_type,
                        'task_run_id': latest_task.task_run.run_id if latest_task.task_run is not None else None,
                        'replayed_from_checkpoint_id': state.get('replayed_from_checkpoint_id'),
                    },
                )
            )
        self.memory.upsert_task(latest_task)
        self._persist_task_run(latest_task)
        # ★ 记忆融合点：任务完成后自动晋升 run → semantic
        self._commit_memory(latest_task.task_id)
        state['task'] = latest_task

    def _checkpoint_after_step(self, state: dict[str, Any], step_id: str, next_route: str) -> dict[str, Any]:
        """为关键 task step 创建 checkpoint，并返回需要回写到 graph state 的增量。

        该回调由图构建层在关键步骤后调用，使 checkpoint 生成逻辑集中在 orchestrator，而不是分散
        到每个节点内部。
        """
        task = cast(TaskDetail, state['task'])
        snapshot = self._build_task_checkpoint_snapshot(state)
        task = self._get_nodes().append_checkpoint(task, step_id=step_id, next_route=next_route, state_snapshot=snapshot)
        return {
            'task': task,
            'checkpoints': list(task.task_run.checkpoints if task.task_run is not None else []),
            'run_events': [item.model_dump(mode='python') for item in task.run_events],
        }

    def _build_task_checkpoint_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """提取 task runtime 最小可 replay 的状态快照。

        快照设计目标是“足够恢复，但不过度膨胀”。因此会保留 task、plan、证据、草稿、审查结果、
        context bundles 和运行事件，但会把 task_run 内已有 checkpoint 清空，避免快照递归嵌套。
        """
        task = cast(TaskDetail, state['task'])
        task.ensure_runtime_contracts()
        task_run = task.task_run.model_copy(deep=True) if task.task_run is not None else None
        if task_run is not None:
            # checkpoint 快照里不再嵌入已有 checkpoints，避免形成无限套娃的持久化结构。
            task_run.checkpoints = []
        return {
            'task': task.model_dump(mode='json'),
            'task_spec': task.task_spec.model_dump(mode='json') if task.task_spec is not None else None,
            'task_run': task_run.model_dump(mode='json') if task_run is not None else None,
            'started_at': state.get('started_at'),
            'current_step_id': task.task_run.current_step_id if task.task_run is not None else state.get('current_step_id'),
            'completed_step_ids': (
                list(task.task_run.completed_step_ids)
                if task.task_run is not None
                else list(state.get('completed_step_ids') or [])
            ),
            'step_runtimes': {
                key: value.model_dump(mode='json')
                for key, value in (
                    task.task_run.step_runtimes.items()
                    if task.task_run is not None
                    else cast(dict[str, Any], state.get('step_runtimes') or {}).items()
                )
            },
            'checkpoints': [
                item.model_dump(mode='json')
                for item in (
                    task.task_run.checkpoints
                    if task.task_run is not None
                    else cast(list[CheckpointRecord], state.get('checkpoints') or [])
                )
            ],
            'run_events': [item.model_dump(mode='json') for item in task.run_events],
            'context_bundles': {
                key: value.model_dump(mode='json')
                for key, value in task.context_bundles.items()
            },
            'plan': state.get('plan').model_dump(mode='json') if state.get('plan') is not None else None,
            'focus_aspects': list(state.get('focus_aspects') or []),
            'pending_plan_step_ids': list(state.get('pending_plan_step_ids') or []),
            'completed_plan_step_ids': list(state.get('completed_plan_step_ids') or []),
            'active_plan_step_id': state.get('active_plan_step_id'),
            'document_context': state.get('document_context'),
            'evidence_pack': state.get('evidence_pack').model_dump(mode='json') if state.get('evidence_pack') is not None else None,
            'analysis': state.get('analysis'),
            'risks': [item.model_dump(mode='json') for item in cast(list[Any], state.get('risks') or [])],
            'grounding_result': state.get('grounding_result').model_dump(mode='json') if state.get('grounding_result') is not None else None,
            'draft_content': state.get('draft_content').model_dump(mode='json') if state.get('draft_content') is not None else None,
            'draft_artifact_id': state.get('draft_artifact_id'),
            'review': state.get('review').model_dump(mode='json') if state.get('review') is not None else None,
            'revise_count': state.get('revise_count'),
            'tool_call_count': state.get('tool_call_count'),
            'exit_criteria_failures': list(state.get('exit_criteria_failures') or []),
            'exit_decision': state.get('exit_decision'),
            'replayed_from_checkpoint_id': state.get('replayed_from_checkpoint_id'),
        }

    def _state_from_checkpoint(self, detail: TaskRunDetail, checkpoint: CheckpointRecord) -> dict[str, Any]:
        """从 task checkpoint 恢复 workflow state。

        这里会把快照中的 JSON 结构重新还原成强类型对象，并给 replay 后的新 task run 分配一个
        新的 `run_id` 后缀，避免覆盖原始运行记录。
        """
        snapshot = checkpoint.state_snapshot
        task = TaskDetail.model_validate(snapshot['task'])
        task.ensure_runtime_contracts()
        if task.task_run is not None:
            task.task_run.run_id = f'{task.task_run.run_id}-replay-{uuid4().hex[:6]}'
            task.task_run.status = 'running'
            task.task_run.started_at = datetime.now(timezone.utc)
            task.task_run.completed_at = None
            task.task_run.checkpoints = [checkpoint]
            task.task_run.step_runtimes = {
                key: StepRuntimeRecord.model_validate(value)
                for key, value in cast(dict[str, Any], snapshot.get('step_runtimes') or {}).items()
            }
        task.context_bundles = {
            key: ContextBundle.model_validate(value)
            for key, value in cast(dict[str, Any], snapshot.get('context_bundles') or {}).items()
        }
        task.run_events = [TaskRunEvent.model_validate(item) for item in snapshot.get('run_events', [])]
        return {
            **snapshot,
            'task': task,
            'task_run': task.task_run,
            'started_at': snapshot.get('started_at'),
            'plan': TaskPlan.model_validate(snapshot['plan']) if snapshot.get('plan') is not None else None,
            'evidence_pack': EvidencePack.model_validate(snapshot['evidence_pack']) if snapshot.get('evidence_pack') is not None else None,
            'risks': [RiskItem.model_validate(item) for item in snapshot.get('risks', [])],
            'grounding_result': (
                GroundingBundle.model_validate(snapshot['grounding_result'])
                if snapshot.get('grounding_result') is not None
                else None
            ),
            'draft_content': (
                ReportArtifactContent.model_validate(snapshot['draft_content'])
                if snapshot.get('draft_content') is not None
                else None
            ),
            'review': ReviewResult.model_validate(snapshot['review']) if snapshot.get('review') is not None else None,
            'context_bundles': {
                key: ContextBundle.model_validate(value)
                for key, value in snapshot.get('context_bundles', {}).items()
            },
            'step_runtimes': {
                key: StepRuntimeRecord.model_validate(value)
                for key, value in snapshot.get('step_runtimes', {}).items()
            },
            'checkpoints': [checkpoint],
            'run_events': [
                *[TaskRunEvent.model_validate(item).model_dump(mode='python') for item in snapshot.get('run_events', [])],
                # replay 本身也要记成一条运行事件，便于后续审计和排障。
                {
                    'event_id': f'revt-{uuid4().hex[:12]}',
                    'name': 'workflow_replayed',
                    'timestamp': datetime.now(timezone.utc),
                    'payload': {
                        'task_run_id': task.task_run.run_id if task.task_run is not None else detail.run_id,
                        'checkpoint_id': checkpoint.checkpoint_id,
                    },
                },
            ],
        }

    def _resolve_checkpoint(self, detail: TaskRunDetail, checkpoint_id: str | None) -> CheckpointRecord:
        """解析要 replay 的 checkpoint。

        未显式指定时，默认使用最后一个 checkpoint，符合用户对“从最近进度继续”的直觉。
        """
        if checkpoint_id is None:
            return detail.checkpoints[-1]
        for item in detail.checkpoints:
            if item.checkpoint_id == checkpoint_id:
                return item
        raise ValueError(f'checkpoint not found: {checkpoint_id}')

    def _persist_task_run(self, task: TaskDetail) -> None:
        """把 task runtime 同步到独立数据面。

        当前任务侧的数据面主要通过 `TaskMemory` 收口，因此这里保持一个极薄的统一持久化入口。
        """
        self.memory.upsert_task(task)

"""查询工作流编排器模块。

负责为 query/chat 及其流式模式提供统一的 graph-driven workflow 入口。该模块本身不实现
检索和回答逻辑，主要承担状态初始化、graph 调用、失败收口和 replay/resume 接缝。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from statistics import median
from time import perf_counter
from typing import Any, Optional, cast
from uuid import uuid4

from app.agents.memory import TaskMemory
from app.agents.tools.base import AgentTool
from app.agents.tools.defaults import build_runtime_rag_tools
from app.agents.tools.registry import ToolRegistry
from app.capabilities.knowledge.contracts import RetrievalQualityReport
from app.core.config import Settings
from app.harness.context import ContextHarness
from app.harness.execution import ExecutionHarness
from app.harness.recovery import RecoveryManager
from app.harness.reflection import ReflectionHarness
from app.harness.hooks import EventBus
from app.harness.guardrails import GuardrailEngine
from app.harness.model_router import ModelRouter
from app.harness.models import ContextBundle
from app.harness.policy import PolicyEngine
from app.harness.react_runtime import BoundedLocalReActRuntime
from app.models.query import (
    ChatRequest,
    QueryRequest,
    QueryResponse,
    QueryResultArtifact,
    QueryResultArtifactContent,
    QueryRunAnalytics,
    QueryRunDetail,
    QueryRunEvent,
    QueryRunSummary,
)
from app.models.session import SessionDetail, SessionSummaryItem, SessionSummaryResponse
from app.models.runtime_contracts import (
    GraphSubgraph,
    GroundedContext,
    MemoryRecord,
    PromptBuildRequest,
    PromptBuildResult,
    PromptSpec,
    dump_result_contract,
    load_result_contract,
)
from app.models.task import CheckpointRecord, ReflectionDecision, TaskRun, TaskSpec
from app.rag.facade import RagFacade
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.runtime_contract_adapters import query_run_event_to_memory_record
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import QueryRunRecord, SSEEvent
from app.workflows.query_events import make_error_event
from app.workflows.query_graph import QueryGraphNodeExecutionError, build_query_graph
from app.workflows.query_runtime import QueryWorkflowRuntime, ensure_query_workflow_runtime
from app.workflows.query_task_adapter import build_query_task_spec
from app.workflows.query_state import QueryGraphState, WorkflowMode, init_query_graph_state
from app.workflows.step_lifecycle import normalize_step_runtimes


class QueryWorkflowExecutionError(RuntimeError):
    """封装 query workflow 失败时的部分运行态。

    与普通异常不同，这个异常会携带 `partial_state`，让流式接口、恢复逻辑和持久化层在工作流
    中途失败时，仍然能够拿到已生成的事件、已完成步骤和最近 checkpoint。
    """

    def __init__(self, message: str, partial_state: QueryGraphState | None = None) -> None:
        super().__init__(message)
        self.partial_state = partial_state


class QueryWorkflowOrchestrator:
    """查询工作流编排器主类。

    主要负责把 query/chat 请求映射到统一 workflow state，并驱动 LangGraph compiled app。
    它本身不承载检索、回答、反思或缓存实现，而是充当“装配层 + 生命周期收口层”：

    - 在入口处构造 `TaskSpec` / `TaskRun` / `QueryGraphState`
    - 在执行期调用 LangGraph 图并统一收口错误
    - 在完成后把运行态持久化为 query run 记录
    - 在恢复场景下负责 checkpoint replay / resume
    """

    def __init__(
        self,
        settings: Settings,
        classic_engine: RagQueryEngine,
        trace: TraceRecorder,
        state: InMemoryState | None = None,
        persistence: SQLiteStateStore | None = None,
        capabilities: dict[str, Any] | None = None,
        registry: ToolRegistry | None = None,
        task_memory: TaskMemory | None = None,
        context_harness: ContextHarness | None = None,
        execution_harness: ExecutionHarness | None = None,
        react_runtime: BoundedLocalReActRuntime | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        reflection_harness: ReflectionHarness | None = None,
        recovery_manager: RecoveryManager | None = None,
        model_router: ModelRouter | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """初始化查询工作流编排器。

        Args:
            settings: 全局配置对象；其中的 query workflow 配置仅保留兼容语义。
            classic_engine: 查询运行时依赖，也作为 LangGraph 节点依赖。
            trace: 链路追踪记录器，用于记录工作流路由与执行状态。
        """
        self.settings = settings
        self.classic_engine = classic_engine
        self.query_runtime: QueryWorkflowRuntime = ensure_query_workflow_runtime(classic_engine)
        self.trace = trace
        self.state = state
        self.persistence = persistence
        self.model_router = model_router or ModelRouter()
        # knowledge / rag 从 capabilities dict 中提取，不再硬编码独立参数。
        self.knowledge_capability = capabilities.get('knowledge') if capabilities else None
        if self.knowledge_capability is None:
            self.knowledge_capability = getattr(classic_engine, 'knowledge_capability', None)
        self.rag_facade = capabilities.get('rag') if capabilities else None
        if self.rag_facade is None and self.knowledge_capability is not None:
            self.rag_facade = RagFacade(self.knowledge_capability)
        self.capabilities = capabilities or {}
        self.registry = registry or ToolRegistry()
        if registry is None:
            for tool in build_runtime_rag_tools():
                self.registry.register(cast(AgentTool, tool))
        self.task_memory = task_memory or TaskMemory(self.state or InMemoryState(), self.persistence)
        self.guardrail_engine = guardrail_engine or GuardrailEngine(self.registry)
        self.policy_engine = policy_engine or PolicyEngine(settings, persistence=self.persistence)
        self.context_harness = context_harness or ContextHarness(self.task_memory, self.registry, settings)
        self.react_runtime = react_runtime or BoundedLocalReActRuntime()
        self.execution_harness = execution_harness or ExecutionHarness(
            self.registry,
            self.task_memory,
            self.trace,
            self.settings,
            self.state or InMemoryState(),
            getattr(classic_engine, 'retrieval_service', None),
            getattr(getattr(classic_engine, 'retrieval_service', None), 'vector_store', None),
            getattr(classic_engine, 'llm', None),
            capabilities=self.capabilities,
            guardrail_engine=self.guardrail_engine,
            policy_engine=self.policy_engine,
            model_router=self.model_router,
            event_bus=event_bus,
        )
        self.reflection_harness = reflection_harness or ReflectionHarness()
        self.recovery_manager = recovery_manager or RecoveryManager()
        self._query_app: Any | None = None
        if self.settings.enable_query_run_auto_recovery:
            self.recover_query_runs(limit=self.settings.query_run_auto_recovery_limit, auto_resume=True)

    def query(self, payload: QueryRequest) -> QueryResponse:
        """执行单轮问答。

        Args:
            payload: 单轮查询请求。

        Returns:
            查询结果响应。
        """

        state = self._invoke_workflow(payload, mode='query')
        return self._require_result(state)

    def chat(self, payload: ChatRequest) -> QueryResponse:
        """执行会话问答。

        Args:
            payload: 多轮会话请求。

        Returns:
            会话查询结果响应。
        """

        state = self._invoke_workflow(payload, mode='chat')
        return self._finalize_chat_workflow(payload, state)

    def stream_query(self, payload: QueryRequest) -> Iterator[SSEEvent]:
        """输出 query SSE 事件流。

        Args:
            payload: 单轮查询请求。

        Yields:
            SSE 事件。
        """

        try:
            state = self._invoke_workflow(payload, mode='query_stream')
            yield from state.get('events', [])
        except QueryWorkflowExecutionError as exc:
            if exc.partial_state is not None:
                yield from self._failure_events_from_state(exc.partial_state)
            yield make_error_event('workflow_failed', str(exc))
        except Exception as exc:
            yield make_error_event('workflow_failed', str(exc))

    def stream_chat(self, payload: ChatRequest) -> Iterator[SSEEvent]:
        """输出 chat SSE 事件流。

        Args:
            payload: 多轮会话请求。

        Yields:
            SSE 事件。
        """

        try:
            state = self._invoke_workflow(payload, mode='chat_stream')
            self._finalize_chat_workflow(payload, state)
            yield from state.get('events', [])
        except QueryWorkflowExecutionError as exc:
            if exc.partial_state is not None:
                yield from self._failure_events_from_state(exc.partial_state)
            yield make_error_event('workflow_failed', str(exc))
        except Exception as exc:
            yield make_error_event('workflow_failed', str(exc))

    def get_session(self, session_id: str) -> SessionDetail | None:
        """读取会话详情。"""

        return self.classic_engine.get_session(session_id)

    def list_sessions(self) -> list[SessionSummaryItem]:
        """列出当前会话。"""

        return self.classic_engine.list_sessions()

    def summarize_session(self, session_id: str) -> SessionSummaryResponse | None:
        """为会话生成摘要。"""

        return self.classic_engine.summarize_session(session_id)

    def list_query_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        mode: str | None = None,
        collection_name: str | None = None,
        recoverable_only: bool = False,
    ) -> list[QueryRunSummary]:
        """列出 query/chat runtime 历史。

        当前实现优先从内存态读取；如果编排器启用了持久化，详情接口会在缺失时再回源 SQLite。
        """
        records = list((self.state.query_runs.values() if self.state is not None else []))
        if status is not None:
            records = [item for item in records if item['status'] == status]
        if mode is not None:
            records = [item for item in records if item['mode'] == mode]
        if collection_name is not None:
            records = [item for item in records if item['collection_name'] == collection_name]
        if recoverable_only:
            records = [item for item in records if item.get('recoverable')]
        records.sort(key=lambda item: item['created_at'], reverse=True)
        sliced = records[offset : offset + limit]
        return [self._to_query_run_summary(item) for item in sliced]

    def get_query_run(self, run_id: str) -> QueryRunDetail | None:
        """读取单个 query/chat runtime 详情。

        若内存态未命中且存在持久化存储，则会尝试回源加载，并回填到内存缓存。
        """
        record = self.state.query_runs.get(run_id) if self.state is not None else None
        if record is None and self.persistence is not None:
            loaded = self.persistence.get_query_run(run_id)
            if loaded is not None and self.state is not None:
                self.state.query_runs[run_id] = cast(QueryRunRecord, loaded)
                record = cast(QueryRunRecord, loaded)
        if record is None:
            return None
        return self._to_query_run_detail(record)

    def replay_query_run(self, run_id: str, checkpoint_id: str | None = None) -> QueryRunDetail:
        """从已持久化的 query run 中选择 checkpoint 并重放。

        当调用方未显式指定 `checkpoint_id` 时，默认选择最后一个 checkpoint 作为重放起点。
        """
        existing = self.get_query_run(run_id)
        if existing is None:
            raise KeyError(run_id)
        checkpoint: CheckpointRecord | None = None
        if checkpoint_id is not None:
            checkpoint = next((item for item in existing.checkpoints if item.checkpoint_id == checkpoint_id), None)
        elif existing.checkpoints:
            checkpoint = existing.checkpoints[-1]
        if checkpoint is None:
            raise ValueError('checkpoint not found')
        state = self.replay_from_checkpoint(checkpoint)
        return self._to_query_run_detail(self._build_query_run_record(state))

    def resume_query_run(self, run_id: str) -> QueryRunDetail:
        """从最近一个可恢复 checkpoint 继续执行 query run。

        与 replay 不同，这里会先把原始 run 标记为已进入恢复流程，避免自动恢复任务重复拾取同一条
        记录。
        """
        existing = self.get_query_run(run_id)
        if existing is None:
            raise KeyError(run_id)
        checkpoint = self._latest_recoverable_checkpoint(existing)
        if checkpoint is None:
            raise ValueError('query run is not recoverable')
        self._mark_query_run_recovered(run_id, checkpoint.checkpoint_id)
        state = self.replay_from_checkpoint(checkpoint)
        return self._to_query_run_detail(self._build_query_run_record(state))

    def recover_query_runs(self, *, limit: int = 20, auto_resume: bool = False) -> list[QueryRunDetail]:
        """扫描可恢复的 query run，并在需要时自动恢复。

        该方法既可以作为只读查询接口使用，也可以在启动时作为自动恢复入口使用。自动恢复失败时，
        会把原 run 标记为不可恢复失败态，避免无限循环重试。
        """
        recoverable = self.list_query_runs(limit=limit, offset=0, recoverable_only=True)
        if not auto_resume:
            details: list[QueryRunDetail] = []
            for item in recoverable:
                detail = self.get_query_run(item.run_id)
                if detail is not None:
                    details.append(detail)
            return details
        recovered: list[QueryRunDetail] = []
        for item in recoverable:
            try:
                recovered.append(self.resume_query_run(item.run_id))
            except Exception as exc:
                detail = self.get_query_run(item.run_id)
                if detail is None:
                    continue
                if self.state is not None:
                    record = self.state.query_runs.get(item.run_id)
                    if record is not None:
                        record['status'] = 'failed'
                        record['recoverable'] = False
                        record['updated_at'] = datetime.now(timezone.utc)
                        record['run_events'] = [
                            *record.get('run_events', []),
                            {
                                'event_id': f'revt-{uuid4().hex[:12]}',
                                'name': 'workflow_recovery_failed',
                                'timestamp': datetime.now(timezone.utc),
                                'payload': {'error': str(exc)},
                            },
                        ]
                        if self.persistence is not None:
                            self.persistence.upsert_query_run(record)
            else:
                self.trace.record('workflow_recovered', {'run_id': item.run_id})
        return recovered

    def get_query_run_analytics(self, *, collection_name: str | None = None) -> QueryRunAnalytics:
        """返回 query run 聚合统计。

        统计口径聚焦运行态观测，主要回答“运行了多少次、成功率如何、平均耗时怎样、常见退出原因是
        什么”这几个问题。
        """
        records = list((self.state.query_runs.values() if self.state is not None else []))
        if collection_name is not None:
            records = [item for item in records if item['collection_name'] == collection_name]
        latencies = [int(cast(int, item['latency_ms'])) for item in records if item.get('latency_ms') is not None]
        mode_counts: dict[str, int] = {}
        answer_mode_counts: dict[str, int] = {}
        exit_reason_counts: dict[str, int] = {}
        for item in records:
            mode_counts[item['mode']] = mode_counts.get(item['mode'], 0) + 1
            answer_mode = str(item.get('answer_mode') or 'unknown')
            answer_mode_counts[answer_mode] = answer_mode_counts.get(answer_mode, 0) + 1
            exit_reason = str((dump_result_contract(item.get('result_contract')) or {}).get('exit_reason') or 'unknown')
            exit_reason_counts[exit_reason] = exit_reason_counts.get(exit_reason, 0) + 1
        return QueryRunAnalytics(
            total_runs=len(records),
            completed_runs=sum(1 for item in records if item['status'] == 'completed'),
            failed_runs=sum(1 for item in records if item['status'] == 'failed'),
            running_runs=sum(1 for item in records if item['status'] == 'running'),
            recoverable_runs=sum(1 for item in records if item.get('recoverable')),
            replayed_runs=sum(1 for item in records if item.get('replayed_from_checkpoint_id') is not None),
            average_latency_ms=(sum(latencies) / len(latencies)) if latencies else 0.0,
            median_latency_ms=float(median(latencies)) if latencies else 0.0,
            mode_counts=mode_counts,
            answer_mode_counts=answer_mode_counts,
            exit_reason_counts=exit_reason_counts,
        )

    def _workflow_name(self) -> str:
        """返回当前 query workflow 的稳定命名。"""
        return 'langgraph'

    def _invoke_workflow(self, payload: QueryRequest | ChatRequest, mode: WorkflowMode) -> QueryGraphState:
        """调用编译后的查询工作流并记录执行轨迹。

        Args:
            payload: 查询或会话请求对象。
            mode: 当前工作流模式。

        Returns:
            工作流结束后的状态字典。
        """
        started = perf_counter()
        workflow_name = self._workflow_name()
        task_spec = build_query_task_spec(payload, mode)
        started_at = datetime.now(timezone.utc)
        # 入口 trace 与 run_events 分开维护：前者面向链路观测，后者面向持久化与恢复。
        self.trace.record(
            'workflow_started',
            {
                'workflow': workflow_name,
                'orchestrator': workflow_name,
                'mode': mode,
                'collection_name': payload.collection_name,
                'use_corrective_rag': getattr(payload, 'use_corrective_rag', False),
                'task_type': task_spec.task_type,
                'task_objective': task_spec.objective,
                'task_steps': [step.step_id for step in task_spec.steps],
            },
        )
        self.trace.record(
            'workflow_routed',
            {
                'workflow': workflow_name,
                'orchestrator': workflow_name,
                'mode': mode,
                'route': 'langgraph_graph',
                'task_type': task_spec.task_type,
            },
        )
        state: QueryGraphState | None = None
        try:
            # Self-RAG 最大重试次数只在请求显式开启且全局配置允许时生效。
            max_retry_count = (
                max(0, int(self.settings.self_rag_max_retry_count))
                if getattr(payload, 'use_corrective_rag', False) and self.settings.enable_self_rag_retry
                else 0
            )
            run_id = f'query-run-{uuid4().hex[:12]}'
            task_run = TaskRun(
                run_id=run_id,
                task_id=run_id,
                status='queued',
                budget=task_spec.run_budget,
                step_specs=list(task_spec.steps),
            )
            task_run.start(started_at)
            state = init_query_graph_state(
                payload,
                mode=mode,
                task_spec=task_spec,
                task_run=task_run,
                max_retry_count=max_retry_count,
            )
            state['run_events'].append(
                {
                    'event_id': f'revt-{uuid4().hex[:12]}',
                    'name': 'workflow_started',
                    'timestamp': started_at,
                    'payload': {
                        'workflow': workflow_name,
                        'orchestrator': workflow_name,
                        'mode': mode,
                        'collection_name': payload.collection_name,
                        'task_type': task_spec.task_type,
                        'task_objective': task_spec.objective,
                        'task_steps': [step.step_id for step in task_spec.steps],
                    },
                }
            )
            self._persist_query_run(state)
            result = self._run_query_runtime(state, initial_route='check_guardrails')
            completed_at = datetime.now(timezone.utc)
            self._finalize_successful_query_state(
                result,
                workflow_name=workflow_name,
                mode=mode,
                collection_name=payload.collection_name,
                task_spec=task_spec,
                completed_at=completed_at,
            )
            result_task_run = result['task_run']
            latency_ms = int((perf_counter() - started) * 1000)
            self.trace.record(
                'workflow_completed',
                {
                    'workflow': workflow_name,
                    'orchestrator': workflow_name,
                    'mode': mode,
                    'collection_name': payload.collection_name,
                    'latency_ms': latency_ms,
                    'status': 'ok',
                    'task_type': task_spec.task_type,
                    'has_result': result.get('result') is not None,
                    'event_count': len(result.get('events', [])),
                    'task_run_id': result_task_run.run_id,
                    'current_step_id': result_task_run.current_step_id,
                    'completed_step_ids': list(result_task_run.completed_step_ids),
                    'step_attempts': dict(result_task_run.step_attempts),
                },
            )
            self._persist_query_run(cast(QueryGraphState, result))
            return result
        except Exception as exc:
            # 无论异常来自图节点内部还是图调用外层，都尽量把局部 state 标记为 failed 并持久化。
            if isinstance(exc, QueryWorkflowExecutionError):
                state = cast(Optional[QueryGraphState], exc.partial_state)
            latency_ms = int((perf_counter() - started) * 1000)
            if state is not None and state.get('task_run') is not None:
                state['task_run'].status = 'failed'
                failed_at = datetime.now(timezone.utc)
                state['task_run'].completed_at = failed_at
                state['run_events'] = [
                    *(state.get('run_events') or []),
                    {
                        'event_id': f'revt-{uuid4().hex[:12]}',
                        'name': 'workflow_completed',
                        'timestamp': failed_at,
                        'payload': {
                            'workflow': workflow_name,
                            'orchestrator': workflow_name,
                            'mode': mode,
                            'collection_name': payload.collection_name,
                            'status': 'error',
                            'task_type': task_spec.task_type,
                            'error': str(exc),
                        },
                    },
                ]
            self.trace.record(
                'workflow_completed',
                {
                    'workflow': workflow_name,
                    'orchestrator': workflow_name,
                    'mode': mode,
                    'collection_name': payload.collection_name,
                    'latency_ms': latency_ms,
                    'status': 'error',
                    'task_type': task_spec.task_type,
                    'error': str(exc),
                    'current_step_id': state.get('current_step_id') if state is not None else None,
                    'completed_step_ids': list(state.get('completed_step_ids') or []) if state is not None else [],
                },
            )
            if state is not None:
                self._persist_query_run(state)
            raise QueryWorkflowExecutionError(str(exc), state) from exc

    # ── 恢复与回放 ────────────────────────────────────────────────────────────

    def replay_from_checkpoint(self, checkpoint: CheckpointRecord) -> QueryGraphState:
        """从指定 checkpoint 继续执行 query workflow。

        该方法会把快照里的 JSON 结构重新还原为强类型模型，并为新一轮运行生成全新的 `run_id`，
        从而把“原始失败 run”和“恢复后新 run”明确区分开。
        """
        snapshot = dict(checkpoint.state_snapshot)
        state = cast(QueryGraphState, snapshot)
        state['request'] = (
            ChatRequest.model_validate(snapshot['request'])
            if snapshot.get('mode') in {'chat', 'chat_stream'}
            else QueryRequest.model_validate(snapshot['request'])
        )
        state['task_spec'] = TaskSpec.model_validate(snapshot['task_spec'])
        state['task_run'] = TaskRun.model_validate(snapshot['task_run'])
        state['task_run'].step_runtimes = normalize_step_runtimes(
            cast(dict[str, Any], snapshot.get('step_runtimes') or {})
        )
        if snapshot.get('reflection_decision') is not None:
            state['reflection_decision'] = ReflectionDecision.model_validate(snapshot['reflection_decision'])
        if snapshot.get('result') is not None:
            state['result'] = QueryResponse.model_validate(snapshot['result'])
        state['memory_records'] = [MemoryRecord.model_validate(item) for item in snapshot.get('memory_records', [])]
        state['prompt_specs'] = [PromptSpec.model_validate(item) for item in snapshot.get('prompt_specs', [])]
        state['prompt_build_requests'] = [PromptBuildRequest.model_validate(item) for item in snapshot.get('prompt_build_requests', [])]
        state['prompt_build_results'] = [PromptBuildResult.model_validate(item) for item in snapshot.get('prompt_build_results', [])]
        state['grounded_context'] = (
            GroundedContext.model_validate(snapshot['grounded_context'])
            if snapshot.get('grounded_context') is not None
            else None
        )
        state['graph_subgraph'] = (
            GraphSubgraph.model_validate(snapshot['graph_subgraph'])
            if snapshot.get('graph_subgraph') is not None
            else None
        )
        state['retrieval_quality_report'] = (
            RetrievalQualityReport.model_validate(snapshot['retrieval_quality_report'])
            if snapshot.get('retrieval_quality_report') is not None
            else None
        )
        state['context_bundles'] = {
            key: ContextBundle.model_validate(value)
            for key, value in cast(dict[str, Any], snapshot.get('context_bundles') or {}).items()
        }
        state['step_runtimes'] = normalize_step_runtimes(cast(dict[str, Any], snapshot.get('step_runtimes') or {}))
        state['checkpoints'] = [
            CheckpointRecord.model_validate(item)
            for item in snapshot.get('checkpoints', [])
        ]
        state['checkpoints'] = [checkpoint]
        state['replayed_from_checkpoint_id'] = checkpoint.checkpoint_id
        state['run_events'] = [QueryRunEvent.model_validate(item).model_dump(mode='python') for item in snapshot.get('run_events', [])]
        # replay 后必须生成新的 TaskRun 身份，避免覆盖旧 run 的历史记录。
        task_run = state['task_run'].model_copy(deep=True)
        task_run.run_id = f'query-run-{uuid4().hex[:12]}'
        task_run.task_id = task_run.run_id
        task_run.checkpoints = [checkpoint]
        task_run.step_runtimes = normalize_step_runtimes(cast(dict[str, Any], snapshot.get('step_runtimes') or {}))
        task_run.status = 'running'
        task_run.started_at = datetime.now(timezone.utc)
        task_run.completed_at = None
        state['task_run'] = task_run
        state['run_events'].append(
            {
                'event_id': f'revt-{uuid4().hex[:12]}',
                'name': 'workflow_replayed',
                'timestamp': datetime.now(timezone.utc),
                'payload': {
                    'workflow': 'langgraph',
                    'mode': state['mode'],
                    'collection_name': state['request'].collection_name,
                    'task_type': state['task_spec'].task_type,
                    'checkpoint_id': checkpoint.checkpoint_id,
                    'step_id': checkpoint.step_id,
                    'next_route': checkpoint.next_route,
                },
            }
        )
        self.trace.record(
            'workflow_replayed',
            {
                'workflow': self._workflow_name(),
                'mode': state['mode'],
                'collection_name': state['request'].collection_name,
                'task_type': state['task_spec'].task_type,
                'checkpoint_id': checkpoint.checkpoint_id,
                'step_id': checkpoint.step_id,
                'next_route': checkpoint.next_route,
            },
        )
        self._persist_query_run(state)
        result = self._run_query_runtime(state, initial_route=checkpoint.next_route)
        self._finalize_successful_query_state(
            result,
            workflow_name=self._workflow_name(),
            mode=state['mode'],
            collection_name=state['request'].collection_name,
            task_spec=state['task_spec'],
            completed_at=datetime.now(timezone.utc),
        )
        self._persist_query_run(result)
        return result

    def _failure_events_from_state(self, state: QueryGraphState) -> list[SSEEvent]:
        """从失败态 state 提取可返回给前端的事件。

        如果节点自身已经写入了 `step_failed` 事件，则直接复用；否则由 orchestrator 补造一条最小
        失败事件，保证流式调用方始终能拿到明确失败步骤。
        """
        events = list(state.get('events', []))
        if any(item.get('event') == 'step_failed' for item in events):
            return events
        step_id = (
            state.get('current_step_id')
            or getattr(state.get('task_run'), 'current_step_id', None)
            or (state['task_spec'].steps[0].step_id if state['task_spec'].steps else None)
        )
        if step_id is None:
            return events
        return [
            *events,
            {
                'event': 'step_failed',
                'data': {
                    'step_id': step_id,
                    'step_index': next(
                        (index for index, step in enumerate(state['task_spec'].steps, start=1) if step.step_id == step_id),
                        None,
                    ),
                    'completed_step_ids': list(state.get('completed_step_ids') or []),
                    'error': state.get('error') or 'workflow_failed',
                },
            },
        ]

    def _run_query_runtime(self, state: QueryGraphState, *, initial_route: str) -> QueryGraphState:
        """用 LangGraph compiled app 执行 query/chat workflow。

        `initial_route` 支持首次执行、checkpoint replay 和恢复三种入口。图节点若抛出携带局部状态
        的异常，这里会立即持久化该局部状态，再继续向上抛出统一异常。
        """
        runtime_state = cast(QueryGraphState, {**state, 'graph_entry_route': initial_route})
        try:
            result = self._get_query_app().invoke(runtime_state)
        except QueryGraphNodeExecutionError as exc:
            partial_state = cast(QueryGraphState, exc.partial_state)
            self._persist_query_run(partial_state)
            raise QueryWorkflowExecutionError(str(exc), partial_state) from exc
        except Exception as exc:
            self._persist_query_run(runtime_state)
            raise QueryWorkflowExecutionError(str(exc), runtime_state) from exc
        result_state = cast(QueryGraphState, result)
        result_state['graph_entry_route'] = None
        return result_state

    def _get_query_app(self) -> Any:
        """懒加载查询工作流 compiled graph。

        编译图对象通常可在同一 orchestrator 生命周期内复用，因此这里做一次构建、多次调用。
        """
        if self._query_app is None:
            self._query_app = build_query_graph(
                self.classic_engine,
                self.trace,
                capabilities=self.capabilities,
                context_harness=self.context_harness,
                execution_harness=self.execution_harness,
                react_runtime=self.react_runtime,
                reflection_harness=self.reflection_harness,
            )
        return self._query_app

    def _finalize_successful_query_state(
        self,
        state: QueryGraphState,
        *,
        workflow_name: str,
        mode: WorkflowMode,
        collection_name: str,
        task_spec: TaskSpec,
        completed_at: datetime,
    ) -> None:
        """收口 query/chat runtime 完成态。

        这里会把步骤完成信息、checkpoint、反思决策和统一完成事件全部补齐回 `TaskRun` 与
        `run_events`，确保后续持久化记录拥有完整闭环。
        """
        task_run = state.get('task_run')
        if task_run is None:
            raise RuntimeError('query workflow finished without task_run')
        task_run.current_step_id = state.get('current_step_id')
        task_run.completed_step_ids = list(state.get('completed_step_ids') or [])
        task_run.step_runtimes = normalize_step_runtimes(cast(dict[str, Any], state.get('step_runtimes') or {}))
        task_run.checkpoints = list(state.get('checkpoints') or [])
        task_run.last_reflection_decision = state.get('reflection_decision')
        task_run.completed_at = completed_at
        task_run.status = 'completed'
        state['task_run'] = task_run
        result_contract = dump_result_contract(state.get('result_contract')) or {}
        run_events = list(state.get('run_events') or [])
        if not any(item.get('name') == 'query_completed' for item in run_events):
            run_events.append(
                {
                    'event_id': f'revt-{uuid4().hex[:12]}',
                    'name': 'query_completed',
                    'timestamp': completed_at,
                    'payload': {
                        'workflow': workflow_name,
                        'mode': mode,
                        'collection_name': collection_name,
                        'task_type': task_spec.task_type,
                        'answer_mode': state.get('answer_mode') or result_contract.get('kind'),
                    },
                }
            )
        if not any(item.get('name') == 'workflow_completed' for item in run_events):
            run_events.append(
                {
                    'event_id': f'revt-{uuid4().hex[:12]}',
                    'name': 'workflow_completed',
                    'timestamp': completed_at,
                    'payload': {
                        'workflow': workflow_name,
                        'orchestrator': workflow_name,
                        'mode': mode,
                        'collection_name': collection_name,
                        'status': 'ok',
                        'task_type': task_spec.task_type,
                        'task_run_id': task_run.run_id,
                        'replayed_from_checkpoint_id': state.get('replayed_from_checkpoint_id'),
                    },
                }
            )
        state['run_events'] = run_events

    def _persist_query_run(self, state: QueryGraphState) -> None:
        """把当前 query runtime 状态同步到内存和 SQLite。

        该方法是 query run 观测与恢复能力的收口点。无论成功、失败还是中途 replay，只要状态被
        更新，都尽量走这里落库。
        """
        if self.state is None:
            return
        record = self._build_query_run_record(state)
        self.state.query_runs[record['run_id']] = record
        if self.persistence is not None:
            self.persistence.upsert_query_run(record)

    def _build_query_run_record(self, state: QueryGraphState) -> QueryRunRecord:
        """把 workflow state 收敛成可持久化的 query run 记录。

        这个转换函数的目标不是最小字段集，而是尽量保留恢复、排障、审计和结果展示所需的上下文，
        包括 prompt 构建记录、grounding 结构、反思决策和结果契约。
        """
        task_run = state['task_run']
        reflection_decision = state.get('reflection_decision')
        result = state.get('result')
        checkpoints = [item.model_dump(mode='json') for item in (state.get('checkpoints') or [])]
        result_contract = dump_result_contract(state.get('result_contract'))
        result_artifact = result.result_artifact if result is not None else None
        run_events = list(state.get('run_events') or [])
        # 若节点层尚未显式生成 memory records，则从 run_events 兜底映射一份可检索的任务记忆。
        memory_records = state.get('memory_records') or [
            query_run_event_to_memory_record(
                item,
                run_id=task_run.run_id,
                collection_name=state['request'].collection_name,
            )
            for item in run_events
        ]
        latency_ms = int(result.latency_ms) if result is not None else None
        return {
            'run_id': task_run.run_id,
            'status': task_run.status,
            'mode': state['mode'],
            'task_type': state['task_spec'].task_type,
            'collection_name': state['request'].collection_name,
            'request_payload': state['request'].model_dump(mode='json'),
            'task_spec': state['task_spec'].model_dump(mode='json'),
            'task_run': task_run.model_dump(mode='json'),
            'checkpoints': checkpoints,
            'run_events': run_events,
            'memory_records': [item.model_dump(mode='json') for item in memory_records],
            'prompt_specs': [item.model_dump(mode='json') for item in (state.get('prompt_specs') or [])],
            'prompt_build_requests': [item.model_dump(mode='json') for item in (state.get('prompt_build_requests') or [])],
            'prompt_build_results': [item.model_dump(mode='json') for item in (state.get('prompt_build_results') or [])],
            'grounded_context': (
                state['grounded_context'].model_dump(mode='json')
                if state.get('grounded_context') is not None
                else None
            ),
            'graph_subgraph': (
                state['graph_subgraph'].model_dump(mode='json')
                if state.get('graph_subgraph') is not None
                else None
            ),
            'retrieval_quality_report': (
                state['retrieval_quality_report'].model_dump(mode='json')
                if state.get('retrieval_quality_report') is not None
                else None
            ),
            'result_contract': result_contract,
            'reflection_decision': reflection_decision.model_dump(mode='json') if reflection_decision is not None else None,
            'result': result.model_dump(mode='json') if result is not None else None,
            'replayed_from_checkpoint_id': state.get('replayed_from_checkpoint_id'),
            'last_checkpoint_id': checkpoints[-1]['checkpoint_id'] if checkpoints else None,
            'answer_mode': cast(Optional[str], (result_contract or {}).get('kind') or state.get('answer_mode')),
            'result_artifact_id': result_artifact.artifact_id if result_artifact is not None else None,
            'result_artifact_type': result_artifact.artifact_type if result_artifact is not None else None,
            'latency_ms': latency_ms,
            'recoverable': self.recovery_manager.is_query_run_recoverable(
                task_run_status=task_run.status,
                checkpoints=checkpoints,
                completed_at=task_run.completed_at,
            ),
            'created_at': task_run.started_at or datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc),
            'completed_at': task_run.completed_at,
        }

    def _to_query_run_summary(self, record: QueryRunRecord) -> QueryRunSummary:
        """把持久化记录转换为 API 摘要模型。"""
        request_payload = cast(dict[str, Any], record['request_payload'])
        return QueryRunSummary(
            run_id=str(record['run_id']),
            status=str(record['status']),
            mode=str(record['mode']),
            task_type=str(record['task_type']),
            collection_name=str(record['collection_name']),
            question=str(request_payload.get('question') or ''),
            created_at=cast(datetime, record['created_at']),
            completed_at=cast(Optional[datetime], record.get('completed_at')),
            checkpoint_count=len(cast(list[dict[str, Any]], record.get('checkpoints') or [])),
            event_count=len(cast(list[dict[str, Any]], record.get('run_events') or [])),
            replayed_from_checkpoint_id=cast(Optional[str], record.get('replayed_from_checkpoint_id')),
            last_checkpoint_id=cast(Optional[str], record.get('last_checkpoint_id')),
            answer_mode=cast(Optional[str], record.get('answer_mode')),
            result_artifact_id=cast(Optional[str], record.get('result_artifact_id')),
            result_artifact_type=cast(Optional[str], record.get('result_artifact_type')),
            latency_ms=cast(Optional[int], record.get('latency_ms')),
            recoverable=bool(record.get('recoverable')),
        )

    def _to_query_run_detail(self, record: QueryRunRecord) -> QueryRunDetail:
        """把持久化记录转换为 API 详情模型。

        与 summary 不同，detail 会把持久化 JSON 重新还原为强类型模型，便于上层接口直接复用。
        """
        return QueryRunDetail(
            **self._to_query_run_summary(record).model_dump(mode='python'),
            request_payload=cast(dict[str, Any], record['request_payload']),
            task_spec=TaskSpec.model_validate(cast(dict[str, Any], record['task_spec'])),
            task_run=TaskRun.model_validate(cast(dict[str, Any], record['task_run'])),
            checkpoints=[CheckpointRecord.model_validate(item) for item in cast(list[dict[str, Any]], record.get('checkpoints') or [])],
            run_events=[QueryRunEvent.model_validate(item) for item in cast(list[dict[str, Any]], record.get('run_events') or [])],
            memory_records=[MemoryRecord.model_validate(item) for item in cast(list[dict[str, Any]], record.get('memory_records') or [])],
            prompt_specs=[PromptSpec.model_validate(item) for item in cast(list[dict[str, Any]], record.get('prompt_specs') or [])],
            prompt_build_requests=[
                PromptBuildRequest.model_validate(item)
                for item in cast(list[dict[str, Any]], record.get('prompt_build_requests') or [])
            ],
            prompt_build_results=[
                PromptBuildResult.model_validate(item)
                for item in cast(list[dict[str, Any]], record.get('prompt_build_results') or [])
            ],
            grounded_context=(
                GroundedContext.model_validate(record['grounded_context'])
                if record.get('grounded_context') is not None
                else None
            ),
            graph_subgraph=(
                GraphSubgraph.model_validate(record['graph_subgraph'])
                if record.get('graph_subgraph') is not None
                else None
            ),
            retrieval_quality_report=(
                RetrievalQualityReport.model_validate(record['retrieval_quality_report'])
                if record.get('retrieval_quality_report') is not None
                else None
            ),
            result_contract=load_result_contract(cast(Optional[dict[str, Any]], record.get('result_contract'))),
            reflection_decision=(
                ReflectionDecision.model_validate(record['reflection_decision'])
                if record.get('reflection_decision') is not None
                else None
            ),
            result=QueryResponse.model_validate(record['result']) if record.get('result') is not None else None,
        )

    def _apply_state_update(self, state: QueryGraphState, update: dict[str, Any]) -> None:
        """把节点返回的增量更新合并回 TypedDict 状态。"""
        cast(dict[str, Any], state).update(update)

    def _mark_query_run_recovered(self, run_id: str, checkpoint_id: str) -> None:
        """把原始 run 标记为已进入恢复流程，避免重复恢复。"""
        if self.state is None:
            return
        record = self.state.query_runs.get(run_id)
        if record is None:
            return
        record = self.recovery_manager.mark_query_run_recovered(record, checkpoint_id)
        self.state.query_runs[run_id] = record
        if self.persistence is not None:
            self.persistence.upsert_query_run(record)

    def _latest_recoverable_checkpoint(self, detail: QueryRunDetail) -> CheckpointRecord | None:
        """返回一个 query run 最近可用于恢复的 checkpoint。"""
        return self.recovery_manager.latest_recoverable_checkpoint(detail)

    def _is_query_run_recoverable(
        self,
        *,
        task_run_status: str,
        checkpoints: list[dict[str, Any]],
        completed_at: datetime | None,
    ) -> bool:
        """判断一个 query run 是否可恢复。"""
        return self.recovery_manager.is_query_run_recoverable(
            task_run_status=task_run_status,
            checkpoints=checkpoints,
            completed_at=completed_at,
        )

    def _finalize_chat_workflow(self, payload: ChatRequest, state: QueryGraphState) -> QueryResponse:
        """补齐 LangGraph 聊天模式下的 trace 记录。

        Args:
            payload: 当前会话请求。
            state: 工作流最终状态。

        Returns:
            最终查询响应。
        """
        # chat 模式的最终响应由 workflow 本身生成，这里只负责补记 chat 维度的 trace。
        result = self._require_result(state)
        guardrail_state = state.get('guardrail_state')
        self.trace.record(
            'chat_completed',
            {
                'workflow': 'langgraph',
                'collection_name': payload.collection_name,
                'session_id': payload.session_id,
                'retrieved_count': result.retrieved_count,
                'latency_ms': result.latency_ms,
                'chat_mode': 'langgraph_workflow',
                'answer_mode': state.get('answer_mode'),
                'task_type': state['task_spec'].task_type,
                'completed_step_ids': list(state.get('completed_step_ids') or []),
                'session_persisted': bool((state.get('metadata') or {}).get('session_persisted')),
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                **self.query_runtime.graph_trace_flags(payload),
                'semantic_cache': state.get('cache_info'),
                'guardrails': self.query_runtime.public_guardrail_state(
                    guardrail_state,
                    state.get('citation_redaction'),
                    state.get('answer_redaction'),
                )
                if guardrail_state is not None
                else {},
            },
        )
        return result

    def _require_result(self, state: QueryGraphState) -> QueryResponse:
        """从工作流状态里取最终结果，缺失时直接抛清晰异常。"""
        result = state.get('result')
        if result is None:
            raise RuntimeError('query workflow finished without result')
        return result

    def _with_query_result_artifact(
        self,
        response: QueryResponse,
        *,
        collection_name: str,
        retrieval_questions: list[str],
        answer_mode: str | None = None,
    ) -> QueryResponse:
        """为 query/chat 响应补齐 artifact-first 交付对象。

        某些旧路径可能只返回传统 `QueryResponse`，这里提供统一补齐能力，把最终答案包装成
        `QueryResultArtifact`，保证结果交付结构与新 workflow 路径一致。
        """
        existing_answer_mode = response.result_artifact.content.answer_mode if response.result_artifact is not None else None
        resolved_answer_mode = str(answer_mode or existing_answer_mode or 'answer')
        artifact = QueryResultArtifact(
            artifact_id=f'qart-{uuid4().hex[:12]}',
            artifact_type='query_answer_artifact',
            created_at=datetime.now(timezone.utc),
            content=QueryResultArtifactContent(
                answer=response.answer,
                answer_mode=resolved_answer_mode,
                citations=list(response.citations),
                grounded=bool(response.citations),
                degraded=resolved_answer_mode in {'no_context', 'guardrail_blocked', 'corrective_rewrite_applied'},
                session_id=response.session_id,
                retrieval_questions=[item for item in retrieval_questions if item],
                metadata={
                    'collection_name': collection_name,
                    'retrieved_count': response.retrieved_count,
                },
            ),
        )
        return response.model_copy(update={'result_artifact': artifact})

"""查询工作流节点模块。

负责定义 LangGraph 查询工作流中的各个节点，把 classic 查询引擎已有的底层能力拆解成
更细粒度的状态转换步骤，便于在图编排中插入缓存、反思、自愈重试和流式事件桥接逻辑。
"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from app.agents.memory import TaskMemory
from app.agents.tools.base import AgentTool, ToolExecutionError
from app.agents.tools.defaults import build_runtime_rag_tools
from app.agents.tools.registry import ToolRegistry
from app.harness.context import ContextHarness
from app.harness.execution import ExecutionHarness
from app.harness.reflection import ReflectionHarness
from app.harness.model_router import ModelRouter
from app.harness.react_runtime import BoundedLocalReActRuntime
from app.models.artifact import EvidencePack
from app.models.task import ReflectionDecision
from app.models.query import (
    ChatRequest,
    CitationItem,
    QueryResponse,
    QueryResultArtifact,
    QueryResultArtifactContent,
)
from app.models.runtime_contracts import ResultContract, dump_result_contract
from app.rag.facade import RagFacade
from app.rag.observability import TraceRecorder
from app.runtime_contract_adapters import (
    build_ad_hoc_prompt_build_result,
    build_ad_hoc_prompt_spec,
    build_prompt_build_request,
    build_retrieval_quality_report,
    citations_to_graph_subgraph,
    evidence_pack_to_grounded_context,
)
from app.workflows.query_events import (
    append_answer_completed_event,
    append_answer_started_event,
    append_cache_hit_event,
    append_checkpoint_created_event,
    append_citation_ready_event,
    append_corrective_check_event,
    append_delta_events,
    append_done_event,
    append_hyde_event,
    append_multi_query_event,
    append_multi_rewrite_event,
    append_retrieval_event,
    append_rewrite_event,
    append_step_completed_event,
    append_step_failed_event,
    append_step_started_event,
    append_start_event,
)
from app.workflows.query_runtime import QueryWorkflowRuntime, ensure_query_workflow_runtime
from app.workflows.step_lifecycle import (
    create_checkpoint,
    dump_step_runtimes,
    mark_step_completed,
    mark_step_failed,
    mark_step_started,
    normalize_step_runtimes,
)
from app.workflows.query_state import QueryGraphState, QueryGraphUpdate


class QueryWorkflowNodes:
    """封装 query workflow 各节点实现。

    该类负责把查询主链路拆成一组可被 LangGraph 调度的细粒度步骤，包括输入护栏、会话上下文
    装载、问题改写、多路检索、缓存复用、证据整理、答案生成、自反思以及会话持久化。同时，
    这里也统一维护步骤生命周期、流式事件、checkpoint、trace 和 Self-RAG 重试控制。
    """

    STEP_NODE_IDS = frozenset(
        {
            'check_guardrails',
            'load_session_context',
            'rewrite_query',
            'expand_queries',
            'lookup_cache',
            'retrieve_evidence',
            'compress_context',
            'grounded_answer',
            'self_reflect',
            'persist_session',
        }
    )
    ORCHESTRATION_NODE_IDS = frozenset(
        {
            'dispatch_query_step',
            'blocked_response',
            'cache_hit_response',
            'retry_retrieve',
            'rewrite_answer',
            'finalize',
        }
    )
    CHECKPOINT_STEP_IDS = frozenset({'check_guardrails', 'retrieve_evidence', 'self_reflect'})

    def __init__(
        self,
        runtime_or_engine: QueryWorkflowRuntime | Any,
        trace: TraceRecorder,
        capabilities: dict[str, Any] | None = None,
        context_harness: ContextHarness | None = None,
        execution_harness: ExecutionHarness | None = None,
        react_runtime: BoundedLocalReActRuntime | None = None,
        reflection_harness: ReflectionHarness | None = None,
    ) -> None:
        """初始化工作流节点集合。

        Args:
            runtime_or_engine: query workflow 运行时，兼容直接传入经典查询引擎。
            trace: 链路追踪记录器。
        """
        self.runtime = ensure_query_workflow_runtime(runtime_or_engine)
        self.trace = trace
        self.model_router = ModelRouter()
        caps = capabilities or {}
        self.knowledge_capability = (
            caps.get('knowledge')
            or getattr(self.runtime, 'knowledge_capability', None)
        )
        registry = ToolRegistry()
        for tool in build_runtime_rag_tools():
            registry.register(cast(AgentTool, tool))
        memory = TaskMemory(self.runtime.state)
        self.context_harness = context_harness or ContextHarness(memory, registry, self.runtime.settings)
        self.execution_harness = execution_harness or ExecutionHarness(
            registry,
            memory,
            trace,
            self.runtime.settings,
            self.runtime.state,
            self.runtime.retrieval_service,
            getattr(self.runtime.retrieval_service, 'vector_store', None),
            self.runtime.llm,
            capabilities=caps,
            guardrail_engine=None,
            policy_engine=None,
            model_router=self.model_router,
        )
        self.rag_facade = caps.get('rag') or RagFacade(self.knowledge_capability)
        self.react_runtime = react_runtime or BoundedLocalReActRuntime()
        self.reflection_harness = reflection_harness or ReflectionHarness()

    def _is_stream_mode(self, state: QueryGraphState) -> bool:
        """判断当前状态是否处于流式输出模式。"""
        return state['mode'] in {'query_stream', 'chat_stream'}

    def _cache_mode(self, state: QueryGraphState) -> str:
        """根据工作流模式映射语义缓存模式名。"""
        if state['mode'] in {'query', 'query_stream'}:
            return 'query'
        return 'chat_stream' if state['mode'] == 'chat_stream' else 'chat'

    def _stream_mode(self, state: QueryGraphState) -> str:
        """返回当前状态对应的流式上下文标识。"""
        return state['mode']

    def _question(self, state: QueryGraphState) -> str:
        """提取当前请求中的用户问题文本。"""
        return state['request'].question.strip()

    def _task_step_ids(self, state: QueryGraphState) -> list[str]:
        """返回当前任务声明的步骤顺序。"""
        return [step.step_id for step in state['task_spec'].steps]

    def _task_step_index(self, state: QueryGraphState, step_id: str) -> int | None:
        """返回步骤在任务计划中的顺序位置。"""
        for index, planned_step_id in enumerate(self._task_step_ids(state), start=1):
            if planned_step_id == step_id:
                return index
        return None

    def _has_task_step(self, state: QueryGraphState, step_id: str) -> bool:
        """判断任务计划中是否声明了某个步骤。"""
        return self._task_step_index(state, step_id) is not None

    def _node_kind(self, name: str) -> str:
        """返回节点的运行时类型。"""
        if name in self.STEP_NODE_IDS:
            return 'step'
        if name in self.ORCHESTRATION_NODE_IDS:
            return 'orchestration'
        return 'runtime'

    def _next_task_step(
        self,
        state: QueryGraphState,
        after_step_id: str | None,
        *,
        candidates: list[str] | None = None,
        default: str = 'finalize',
    ) -> str:
        """根据 `TaskSpec.steps` 的声明顺序选择下一个节点。

        该 helper 主要服务于 orchestration 分支。它会从给定步骤之后继续向后查找，必要时再按
        `candidates` 做白名单过滤，从而把“下一跳应该去哪里”的判断保持在任务声明层，而不是散落
        在各个节点内部。
        """
        step_ids = self._task_step_ids(state)
        start_index = 0
        if after_step_id is not None:
            try:
                start_index = step_ids.index(after_step_id) + 1
            except ValueError:
                start_index = 0
        allowed = set(candidates) if candidates is not None else None
        for step_id in step_ids[start_index:]:
            if allowed is None or step_id in allowed:
                return step_id
        return default

    def _guardrails_public(
        self,
        state: QueryGraphState,
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """返回可安全暴露给事件与 trace 的护栏状态。

        Args:
            state: 当前工作流状态。
            citation_redaction: 可选的引用脱敏结果。
            answer_redaction: 可选的答案脱敏结果。

        Returns:
            经过裁剪后的护栏状态字典。
        """
        guardrail_state = state.get('guardrail_state')
        if guardrail_state is None:
            return {}
        return self.runtime.public_guardrail_state(guardrail_state, citation_redaction, answer_redaction)

    def _append_run_event(self, state: QueryGraphState, name: str, **payload: Any) -> None:
        """向当前 query runtime 追加一条结构化运行事件。

        与 SSE 事件不同，这里的 run event 面向持久化、回放和排障，强调结构化字段，而不是前端
        消费体验。
        """
        events = list(state.get('run_events') or [])
        events.append(
            {
                'event_id': f'revt-{uuid4().hex[:12]}',
                'name': name,
                'timestamp': datetime.now(timezone.utc),
                'payload': payload,
            }
        )
        state['run_events'] = events

    def _orchestration_route_update(self, route: str) -> QueryGraphUpdate:
        """生成统一 orchestration 下一跳更新。"""
        return {'orchestration_next_route': route}

    def validate_node_contract(self, node_name: str, update: QueryGraphUpdate) -> None:
        """校验 step 节点与 orchestration 节点的写入契约。

        设计上只有真正的步骤节点才允许推进 `completed_step_ids`、写入 `step_runtimes` 或落地
        `reflection_decision`。orchestration 节点只负责路由和拼接下一跳，不应偷偷改写步骤生命周期。
        """
        if node_name in self.ORCHESTRATION_NODE_IDS and node_name != 'dispatch_query_step':
            forbidden_fields = {'completed_step_ids', 'step_runtimes', 'reflection_decision'}
            invalid = forbidden_fields.intersection(update.keys())
            if invalid:
                raise RuntimeError(
                    f'orchestration node `{node_name}` cannot write step runtime fields: {sorted(invalid)}'
                )
        if node_name in self.STEP_NODE_IDS and 'orchestration_next_route' in update:
            raise RuntimeError(f'step node `{node_name}` cannot write orchestration_next_route directly')

    def _record_node(self, name: str, state: QueryGraphState, **payload: Any) -> None:
        """记录单个节点执行完成事件。

        Args:
            name: 节点名称。
            state: 当前工作流状态。
            **payload: 需要附加写入 trace 的额外字段。
        """
        request = state['request']
        step_index = self._task_step_index(state, name)
        self._append_run_event(
            state,
            'workflow_node_completed',
            node=name,
            node_kind=self._node_kind(name),
            mode=state['mode'],
            collection_name=request.collection_name,
            task_type=state['task_spec'].task_type,
            task_step_id=name if step_index is not None else None,
            task_step_index=step_index,
            task_step_count=len(self._task_step_ids(state)),
            completed_step_ids=list(state.get('completed_step_ids') or []),
            **payload,
        )
        self.trace.record(
            'workflow_node_completed',
            {
                'workflow': 'langgraph',
                'node': name,
                'node_kind': self._node_kind(name),
                'mode': state['mode'],
                'collection_name': request.collection_name,
                'task_type': state['task_spec'].task_type,
                'task_step_id': name if step_index is not None else None,
                'task_step_index': step_index,
                'task_step_count': len(self._task_step_ids(state)),
                'completed_step_ids': list(state.get('completed_step_ids') or []),
                **payload,
            },
        )

    def _record_route(self, state: QueryGraphState, route: str, *, from_step: str | None = None) -> str:
        """记录条件路由决策，并返回路由结果。

        Args:
            state: 当前工作流状态。
            route: 本次路由结果。

        Returns:
            原样返回传入的路由结果，便于条件边直接复用。
        """
        request = state['request']
        self._append_run_event(
            state,
            'workflow_routed',
            mode=state['mode'],
            route=route,
            collection_name=request.collection_name,
            task_type=state['task_spec'].task_type,
            from_step=from_step,
            to_task_step=route if self._has_task_step(state, route) else None,
        )
        self.trace.record(
            'workflow_routed',
            {
                'workflow': 'langgraph',
                'mode': state['mode'],
                'route': route,
                'collection_name': request.collection_name,
                'task_type': state['task_spec'].task_type,
                'from_step': from_step,
                'to_task_step': route if self._has_task_step(state, route) else None,
            },
        )
        return route

    def _step_started_update(self, state: QueryGraphState, step_id: str) -> QueryGraphUpdate:
        """生成 step 启动时的运行态更新。"""
        task_run = state['task_run'].model_copy(deep=True)
        runtime = mark_step_started(task_run, step_id)
        step_runtimes = normalize_step_runtimes(dict(task_run.step_runtimes))
        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_step_started_event(
                events,
                step_id=step_id,
                step_index=self._task_step_index(state, step_id),
                completed_step_ids=list(state.get('completed_step_ids') or []),
            )
        self._append_run_event(
            state,
            'workflow_step_started',
            mode=state['mode'],
            collection_name=state['request'].collection_name,
            task_type=state['task_spec'].task_type,
            task_step_id=step_id,
            task_step_index=self._task_step_index(state, step_id),
            attempt_count=runtime.attempt_count,
            completed_step_ids=list(state.get('completed_step_ids') or []),
        )
        self.trace.record(
            'workflow_step_started',
            {
                'workflow': 'langgraph',
                'mode': state['mode'],
                'collection_name': state['request'].collection_name,
                'task_type': state['task_spec'].task_type,
                'task_step_id': step_id,
                'task_step_index': self._task_step_index(state, step_id),
                'attempt_count': runtime.attempt_count,
            },
        )
        return {
            'current_step_id': step_id,
            'events': events,
            'task_run': task_run,
            'step_runtimes': step_runtimes,
        }

    def _step_progress_update(
        self,
        state: QueryGraphState,
        step_id: str,
        *,
        events: list[dict[str, Any]] | None = None,
        exit_reason: str = 'completed',
        fallback_action_applied: str | None = None,
        degraded: bool = False,
        skipped: bool = False,
        reflection_decision: ReflectionDecision | None = None,
    ) -> QueryGraphUpdate:
        """生成当前节点对应的步骤进度更新。"""
        completed = list(state.get('completed_step_ids') or [])
        if step_id not in completed:
            completed.append(step_id)
        task_run = state['task_run'].model_copy(deep=True)
        runtime = mark_step_completed(
            task_run,
            step_id,
            completed_step_ids=list(completed),
            exit_reason=exit_reason,
            fallback_action_applied=fallback_action_applied,
            degraded=degraded,
            skipped=skipped,
            reflection_decision=reflection_decision,
        )
        step_runtimes = normalize_step_runtimes(dict(task_run.step_runtimes))
        event_payload = events if events is not None else state.get('events', [])
        if self._is_stream_mode(state):
            event_payload = append_step_completed_event(
                event_payload,
                step_id=step_id,
                step_index=self._task_step_index(state, step_id),
                completed_step_ids=list(completed),
                exit_reason=exit_reason,
                fallback_action_applied=fallback_action_applied,
                degraded=degraded,
                skipped=skipped,
                attempt_count=runtime.attempt_count,
            )
        self._append_run_event(
            state,
            'workflow_step_completed',
            mode=state['mode'],
            collection_name=state['request'].collection_name,
            task_type=state['task_spec'].task_type,
            task_step_id=step_id,
            task_step_index=self._task_step_index(state, step_id),
            completed_step_ids=list(completed),
            attempt_count=runtime.attempt_count,
            exit_reason=exit_reason,
            fallback_action_applied=fallback_action_applied,
            degraded=degraded,
            skipped=skipped,
        )
        self.trace.record(
            'workflow_step_completed',
            {
                'workflow': 'langgraph',
                'mode': state['mode'],
                'collection_name': state['request'].collection_name,
                'task_type': state['task_spec'].task_type,
                'task_step_id': step_id,
                'task_step_index': self._task_step_index(state, step_id),
                'completed_step_ids': list(completed),
                'attempt_count': runtime.attempt_count,
                'exit_reason': exit_reason,
                'fallback_action_applied': fallback_action_applied,
                'degraded': degraded,
                'skipped': skipped,
            },
        )
        payload: QueryGraphUpdate = {
            'current_step_id': step_id,
            'completed_step_ids': completed,
            'task_run': task_run,
            'step_runtimes': step_runtimes,
        }
        if events is not None or self._is_stream_mode(state):
            payload['events'] = event_payload
        if reflection_decision is not None:
            payload['reflection_decision'] = reflection_decision
        return {
            **payload,
        }

    def _next_pending_task_step(
        self,
        state: QueryGraphState,
        *,
        candidates: list[str] | None = None,
        default: str = 'finalize',
    ) -> str:
        """根据 TaskSpec 和完成进度选择下一个待执行步骤。"""
        completed = set(state.get('completed_step_ids') or [])
        allowed = set(candidates) if candidates is not None else None
        for step_id in self._task_step_ids(state):
            if step_id in completed:
                continue
            if allowed is None or step_id in allowed:
                return step_id
        return default

    def _self_rag_retry_enabled(self, state: QueryGraphState) -> bool:
        """判断当前状态是否允许触发 Self-RAG 重检索。"""
        request = state['request']
        return bool(request.use_corrective_rag and self.runtime.self_rag_retry_enabled())

    def _build_retry_questions(self, state: QueryGraphState) -> list[str]:
        """基于反思结果构造下一轮重检索问题列表。

        会优先加入更偏“事实支撑”的提示，推动下一轮检索召回更直接的证据。

        Args:
            state: 当前工作流状态。

        Returns:
            下一轮重检索使用的问题列表。
        """
        guardrail_state = state.get('guardrail_state')
        if guardrail_state is None:
            raise RuntimeError('workflow state missing guardrail_state before retry_retrieve')
        base_question = guardrail_state['sanitized_question']
        rewritten_question = self.runtime.prepare_retrieval_question(
            base_question,
            use_query_rewrite=True,
            trace_context='self_rag_retry',
        )
        retry_prompt = f'{rewritten_question}\n请优先返回能直接支撑答案的事实、定义、接口说明或参数约束。'.strip()
        existing = state.get('retrieval_questions') or []
        queries: list[str] = []
        seen: set[str] = set()
        for item in [retry_prompt, rewritten_question, *existing]:
            cleaned = item.strip()
            if not cleaned:
                continue
            normalized = cleaned.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            queries.append(cleaned)
        return queries[:6]

    def _build_reflection_decision(self, state: QueryGraphState, corrective_info: dict[str, Any]) -> ReflectionDecision:
        """把 reflection 路由结果收口为结构化决策对象。"""
        request = state['request']
        retry_count = int(state.get('retry_count') or 0)
        max_retry_count = int(state.get('max_retry_count') or 0)
        min_grounding_confidence = self.runtime.self_rag_min_grounding_confidence()
        return self.reflection_harness.build_query_reflection_decision(
            request=request,
            corrective_info=corrective_info,
            retry_count=retry_count,
            max_retry_count=max_retry_count,
            retry_enabled=self._self_rag_retry_enabled(state),
            min_grounding_confidence=min_grounding_confidence,
        )

    def _checkpoint_state_snapshot(self, state: QueryGraphState) -> dict[str, Any]:
        """提取最小可 replay 的状态快照。"""
        task_run = state['task_run'].model_copy(deep=True)
        task_run.checkpoints = []
        snapshot: dict[str, Any] = {
            'mode': state['mode'],
            'request': state['request'],
            'task_spec': state['task_spec'],
            'task_run': task_run,
            'metadata': dict(state.get('metadata') or {}),
            'current_step_id': state.get('current_step_id'),
            'completed_step_ids': list(state.get('completed_step_ids') or []),
            'step_runtimes': dump_step_runtimes(cast(dict[str, Any], state.get('step_runtimes') or {})),
            'checkpoint_ids': [item.checkpoint_id for item in (state.get('checkpoints') or [])],
            'context_bundles': {
                key: value.model_dump(mode='json')
                for key, value in cast(dict[str, Any], state.get('context_bundles') or {}).items()
            },
            'memory_records': [item.model_dump(mode='json') for item in (state.get('memory_records') or [])],
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
            'reflection_decision': state.get('reflection_decision'),
            'result_contract': dump_result_contract(state.get('result_contract')),
            'guardrail_state': state.get('guardrail_state'),
            'rewrite_info': state.get('rewrite_info'),
            'multi_rewrite_info': state.get('multi_rewrite_info'),
            'multi_query_info': state.get('multi_query_info'),
            'hyde_info': state.get('hyde_info'),
            'retrieval_questions': list(state.get('retrieval_questions') or []),
            'cache_question': state.get('cache_question'),
            'cache_info': state.get('cache_info'),
            'cache_hit': state.get('cache_hit'),
            'citations': list(state.get('citations') or []),
            'citation_redaction': state.get('citation_redaction'),
            'contexts': list(state.get('contexts') or []),
            'compression_info': state.get('compression_info'),
            'prompt': state.get('prompt'),
            'raw_answer': state.get('raw_answer'),
            'raw_answer_mode': state.get('raw_answer_mode'),
            'answer': state.get('answer'),
            'answer_mode': state.get('answer_mode'),
            'answer_redaction': state.get('answer_redaction'),
            'corrective_info': state.get('corrective_info'),
            'retry_count': state.get('retry_count'),
            'max_retry_count': state.get('max_retry_count'),
            'self_rag_decision': state.get('self_rag_decision'),
            'retry_reason': state.get('retry_reason'),
            'retrieval_seed_question': state.get('retrieval_seed_question'),
            'events': list(state.get('events') or []),
            'run_events': list(state.get('run_events') or []),
        }
        return snapshot

    def _checkpoint_update(
        self,
        state: QueryGraphState,
        step_id: str,
        *,
        next_route: str,
        merged_state: QueryGraphState,
    ) -> QueryGraphUpdate:
        """为关键 step 创建 checkpoint。"""
        if step_id not in self.CHECKPOINT_STEP_IDS:
            return {}
        checkpoint = create_checkpoint(
            step_id=step_id,
            next_route=next_route,
            completed_step_ids=list(merged_state.get('completed_step_ids') or []),
            state_snapshot=self._checkpoint_state_snapshot(merged_state),
        )
        checkpoints = [*(state.get('checkpoints') or []), checkpoint]
        task_run = merged_state['task_run'].model_copy(deep=True)
        task_run.checkpoints = list(checkpoints)
        events = list(merged_state.get('events') or [])
        if self._is_stream_mode(merged_state):
            events = append_checkpoint_created_event(
                events,
                checkpoint_id=checkpoint.checkpoint_id,
                step_id=step_id,
                next_route=next_route,
            )
        self._append_run_event(
            state,
            'query_checkpoint_created',
            mode=merged_state['mode'],
            collection_name=merged_state['request'].collection_name,
            task_type=merged_state['task_spec'].task_type,
            checkpoint_id=checkpoint.checkpoint_id,
            step_id=step_id,
            next_route=next_route,
            completed_step_ids=list(checkpoint.completed_step_ids),
        )
        self.trace.record(
            'query_checkpoint_created',
            {
                'workflow': 'langgraph',
                'mode': merged_state['mode'],
                'collection_name': merged_state['request'].collection_name,
                'task_type': merged_state['task_spec'].task_type,
                'checkpoint_id': checkpoint.checkpoint_id,
                'step_id': step_id,
                'next_route': next_route,
                'completed_step_ids': list(checkpoint.completed_step_ids),
            },
        )
        return {
            'checkpoints': checkpoints,
            'task_run': task_run,
            'events': events,
        }

    def _step_spec(self, state: QueryGraphState, step_id: str):
        """按 `step_id` 读取当前任务声明中的步骤规格。"""
        for step in state['task_spec'].steps:
            if step.step_id == step_id:
                return step
        raise RuntimeError(f'step spec not found for {step_id}')

    def _build_query_context_bundle(self, state: QueryGraphState, step_id: str):
        """为指定查询步骤构造上下文包并写回状态。

        Context Bundle 作为 query workflow 与 harness / tool / prompt 层之间的通用上下文载体，
        会在后续工具调用、提示词构造与运行态审计中复用。
        """
        step_spec = self._step_spec(state, step_id)
        bundle = self.context_harness.build_query_context(cast(dict[str, Any], state), step_spec)
        context_bundles = dict(state.get('context_bundles') or {})
        context_bundles[step_id] = bundle
        state['context_bundles'] = context_bundles
        return bundle

    def _build_evidence_gap_fallback(
        self,
        state: QueryGraphState,
        requested_focus_aspects: list[str],
        exc: ToolExecutionError,
    ) -> EvidencePack:
        """在证据检索工具失败时构造空证据包降级结果。

        这里不会直接吞掉错误语义，而是把失败原因映射为 `missing_aspects`，让后续答案生成或结果
        展示阶段能够显式披露“缺什么、为什么缺”。
        """
        missing_aspects = requested_focus_aspects or [str(exc.code)]
        return EvidencePack(
            task_id=state['task_run'].run_id,
            evidence_items=[],
            coverage_score=0.0,
            missing_aspects=missing_aspects[:5],
        )

    def _step_failed_update(self, state: QueryGraphState, step_id: str, exc: Exception) -> QueryGraphUpdate:
        """在节点抛错时生成失败态运行记录。"""
        completed = list(state.get('completed_step_ids') or [])
        task_run = state['task_run'].model_copy(deep=True)
        runtime = mark_step_failed(task_run, step_id, completed_step_ids=list(completed), error=str(exc))
        step_runtimes = normalize_step_runtimes(dict(task_run.step_runtimes))
        task_run.status = 'failed'
        events = list(state.get('events') or [])
        if self._is_stream_mode(state):
            events = append_step_failed_event(
                events,
                step_id=step_id,
                step_index=self._task_step_index(state, step_id),
                completed_step_ids=list(completed),
                error=str(exc),
                attempt_count=runtime.attempt_count,
            )
        self._append_run_event(
            state,
            'workflow_step_failed',
            mode=state['mode'],
            collection_name=state['request'].collection_name,
            task_type=state['task_spec'].task_type,
            task_step_id=step_id,
            task_step_index=self._task_step_index(state, step_id),
            completed_step_ids=list(completed),
            attempt_count=runtime.attempt_count,
            error=str(exc),
        )
        self.trace.record(
            'workflow_step_failed',
            {
                'workflow': 'langgraph',
                'mode': state['mode'],
                'collection_name': state['request'].collection_name,
                'task_type': state['task_spec'].task_type,
                'task_step_id': step_id,
                'task_step_index': self._task_step_index(state, step_id),
                'completed_step_ids': list(completed),
                'attempt_count': runtime.attempt_count,
                'error': str(exc),
            },
        )
        return {
            'current_step_id': step_id,
            'task_run': task_run,
            'step_runtimes': step_runtimes,
            'events': events,
            'error': str(exc),
        }

    def handle_node_exception(self, node_name: str, state: QueryGraphState, exc: Exception) -> None:
        """在 graph node 抛错时回写失败态信息与事件。"""
        if node_name not in self.STEP_NODE_IDS:
            return
        state.update(self._step_failed_update(state, node_name, exc))

    def check_guardrails(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行输入护栏检查，并初始化本轮 query 的基础运行态。

        这是查询主链的入口步骤。它除了判断请求是否需要被拦截，还会产出后续步骤复用的
        `guardrail_state`、可持久化问题文本、首个检索种子问题，以及流式场景下的 `start` 事件。
        """

        request = state['request']
        question = self._question(state)
        guardrail_state = self.runtime.check_guardrails(question, request, self._stream_mode(state))
        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_start_event(
                events,
                mode=state['mode'],
                collection_name=request.collection_name,
                session_id=request.session_id,
                use_query_rewrite=request.use_query_rewrite,
                use_multi_query=request.use_multi_query,
                multi_query_count=request.multi_query_count,
                use_multi_rewrite=request.use_multi_rewrite,
                multi_rewrite_count=request.multi_rewrite_count,
                use_hybrid_retrieval=request.use_hybrid_retrieval,
                use_rerank=request.use_rerank,
                use_hyde=request.use_hyde,
                use_long_context_reorder=request.use_long_context_reorder,
                use_parent_chunk_retrieval=request.use_parent_chunk_retrieval,
                use_question_oriented_index=request.use_question_oriented_index,
                use_corrective_rag=request.use_corrective_rag,
                **self.runtime.graph_trace_flags(request),
                use_context_compression=self.runtime.use_context_compression(request),
                guardrails=self.runtime.public_guardrail_state(guardrail_state),
            )
        self._record_node('check_guardrails', state, blocked=guardrail_state['blocked'])
        payload: QueryGraphUpdate = {
            'guardrail_state': guardrail_state,
            'cache_question': self.runtime.question_for_storage(question, guardrail_state),
            'retrieval_seed_question': guardrail_state['sanitized_question'],
            'events': events,
        }
        payload.update(
            self._step_progress_update(
                state,
                'check_guardrails',
                events=events,
                exit_reason='guardrail_blocked' if guardrail_state['blocked'] else 'completed',
            )
        )
        payload.update(
            self._checkpoint_update(
                state,
                'check_guardrails',
                next_route='blocked_response' if guardrail_state['blocked'] else 'dispatch_query_step',
                merged_state=cast(QueryGraphState, {**state, **payload}),
            )
        )
        return payload

    def route_guardrail(self, state: QueryGraphState) -> str:
        """根据护栏结果决定进入短路分支还是正常主链。"""

        guardrail_state = state.get('guardrail_state') or {}
        if guardrail_state.get('blocked'):
            return self._record_route(state, 'blocked_response', from_step='check_guardrails')
        return self._record_route(state, 'dispatch_query_step', from_step='check_guardrails')

    def dispatch_query_step(self, state: QueryGraphState) -> QueryGraphUpdate:
        """按 `TaskSpec.steps` 和当前完成进度分发下一个正常步骤。

        该节点本身不执行业务逻辑，只负责选出下一步，并在目标是正式 step 时触发统一的
        `step_started` 生命周期更新。
        """

        next_step = self._next_pending_task_step(state)
        self._record_node('dispatch_query_step', state, next_step=next_step)
        if self._has_task_step(state, next_step):
            return self._step_started_update(state, next_step)
        return {'current_step_id': state.get('current_step_id')}

    def route_query_step(self, state: QueryGraphState) -> str:
        """为正常步骤主链选择下一个 step handler。"""

        return self._record_route(
            state,
            self._next_pending_task_step(state),
            from_step='dispatch_query_step',
        )

    def route_orchestration(self, state: QueryGraphState, *, from_step: str) -> str:
        """读取统一 orchestration 下一跳。

        像 `blocked_response`、`cache_hit_response`、`retry_retrieve` 这类节点不会自己决定完整主链，
        而是把下一跳写入 `orchestration_next_route`，再由这里统一读取。
        """
        route = state.get('orchestration_next_route') or 'finalize'
        return self._record_route(state, route, from_step=from_step)

    def load_session_context(self, state: QueryGraphState) -> QueryGraphUpdate:
        """基于会话历史构造本轮检索种子问题。

        多轮会话模式下，真正参与检索的问题往往需要携带历史上下文补全语义。该步骤不直接检索，只
        负责把当前轮用户问题改写成适合检索的会话上下文问题。
        """

        request = cast(ChatRequest, state['request'])
        guardrail_state = state.get('guardrail_state')
        if guardrail_state is None:
            raise RuntimeError('workflow state missing guardrail_state before load_session_context')
        retrieval_seed_question = self.runtime.build_chat_retrieval_question(
            request.session_id,
            guardrail_state['sanitized_question'],
        )
        self._record_node('load_session_context', state, session_id=request.session_id)
        payload: QueryGraphUpdate = {
            'retrieval_seed_question': retrieval_seed_question,
        }
        payload.update(self._step_progress_update(state, 'load_session_context'))
        return payload

    def blocked_response(self, state: QueryGraphState) -> QueryGraphUpdate:
        """构造被护栏拦截的最终回答。"""

        request = state['request']
        answer = self.runtime.guardrail_block_message()
        answer_redaction = self.runtime.empty_redaction_state(self.runtime.use_pii_redaction(request))
        corrective_info = self.runtime.empty_corrective_info()
        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_answer_started_event(
                events,
                retrieved_count=0,
                has_citations=False,
            )
        self._record_node('blocked_response', state)
        return {
            **self._orchestration_route_update(
                self._next_task_step(state, 'check_guardrails', candidates=['persist_session'], default='finalize')
            ),
            'events': events,
            'citations': [],
            'citation_redaction': self.runtime.empty_redaction_state(self.runtime.use_pii_redaction(request)),
            'compression_info': {
                'enabled': False,
                'original_chunk_count': 0,
                'compressed_chunk_count': 0,
                'original_sentence_count': 0,
                'compressed_sentence_count': 0,
                'original_char_count': 0,
                'compressed_char_count': 0,
                'strategy': 'disabled',
            },
            'answer': answer,
            'answer_mode': 'guardrail_blocked',
            'answer_redaction': answer_redaction,
            'corrective_info': corrective_info,
            'result_contract': ResultContract(kind='guardrail_blocked', exit_reason='guardrail_blocked'),
        }

    def rewrite_query(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行 query rewrite。"""

        request = state['request']
        question = state.get('retrieval_seed_question') or self._question(state)
        retrieval_question, rewrite_info = self.runtime.resolve_rewrite_info(
            question,
            request.use_query_rewrite,
            self._stream_mode(state),
        )
        events = state.get('events', [])
        if self._is_stream_mode(state) and rewrite_info is not None:
            events = append_rewrite_event(
                events,
                original_query=rewrite_info['original_query'],
                normalized_query=rewrite_info['normalized_query'],
                rewritten_query=rewrite_info['rewritten_query'],
                applied_rules=rewrite_info['applied_rules'],
                expanded_terms=rewrite_info['expanded_terms'],
                changed=rewrite_info['changed'],
            )
        self._record_node('rewrite_query', state, changed=bool(rewrite_info and rewrite_info.get('changed')))
        payload: QueryGraphUpdate = {
            'rewrite_info': rewrite_info,
            'retrieval_questions': [retrieval_question],
            'events': events,
        }
        payload.update(self._step_progress_update(state, 'rewrite_query', events=events))
        return payload

    def expand_queries(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行 multi-rewrite、multi-query 和 HyDE 扩展。

        该节点负责把单问题检索扩展成多问题检索，并将相关元信息同步写入工作流状态，
        供后续检索与流式事件输出复用。
        """

        request = state['request']
        answer_question = self._question(state)
        retrieval_questions = list(state.get('retrieval_questions') or [answer_question])
        events = state.get('events', [])

        retrieval_questions, multi_rewrite_info = self.runtime.maybe_apply_multi_rewrite(
            request,
            retrieval_questions[0],
            answer_question,
            self._stream_mode(state),
        )
        if self._is_stream_mode(state) and multi_rewrite_info is not None:
            events = append_multi_rewrite_event(events, **multi_rewrite_info)

        multi_query_questions, multi_query_info = self.runtime.maybe_apply_multi_query(
            request,
            retrieval_questions[0],
            answer_question,
            self._stream_mode(state),
        )
        if self._is_stream_mode(state) and multi_query_info is not None:
            events = append_multi_query_event(events, **multi_query_info)
        if request.use_multi_query and multi_query_info is not None and multi_query_info.get('enabled'):
            retrieval_questions = multi_query_questions

        primary_question, hyde_info = self.runtime.maybe_apply_hyde(
            request,
            retrieval_questions[0],
            answer_question,
            self._stream_mode(state),
        )
        if self._is_stream_mode(state) and hyde_info is not None:
            events = append_hyde_event(events, **hyde_info)
        retrieval_questions = [primary_question, *retrieval_questions[1:]] if retrieval_questions else [primary_question]
        if request.use_hyde and hyde_info is not None and hyde_info.get('enabled'):
            retrieval_questions = [primary_question]

        self._record_node('expand_queries', state, query_count=len(retrieval_questions))
        payload: QueryGraphUpdate = {
            'multi_rewrite_info': multi_rewrite_info,
            'multi_query_info': multi_query_info,
            'hyde_info': hyde_info,
            'retrieval_questions': retrieval_questions,
            'events': events,
        }
        payload.update(self._step_progress_update(state, 'expand_queries', events=events))
        return payload

    def lookup_cache(self, state: QueryGraphState) -> QueryGraphUpdate:
        """查找语义缓存。

        缓存命中时，这里会直接把缓存中的 citations、answer 和压缩信息桥接为工作流状态，
        让后续节点仍可按统一接口收尾。
        """

        request = state['request']
        cache_question = state.get('cache_question') or self._question(state)
        cached_response, cache_info = self.runtime.lookup_semantic_cache(
            request,
            cache_question,
            self._cache_mode(state),
        )
        payload: QueryGraphUpdate = {
            'cache_info': cache_info,
            'cache_hit': cached_response is not None,
        }
        if cached_response is not None:
            # 缓存命中后仍然补齐 contexts / citations 等字段，保证 finalize 节点无需区分来源。
            contexts, compression_info = self.runtime.prepare_answer_context(
                self._question(state),
                cached_response.citations,
                request,
            )
            payload.update(
                {
                    'citations': cached_response.citations,
                    'citation_redaction': self.runtime.empty_redaction_state(
                        self.runtime.use_pii_redaction(request)
                    ),
                    'contexts': contexts,
                    'compression_info': compression_info,
                    'answer': cached_response.answer,
                    'answer_mode': 'semantic_cache_hit',
                    'answer_redaction': self.runtime.empty_redaction_state(
                        self.runtime.use_pii_redaction(request)
                    ),
                    'corrective_info': self.runtime.empty_corrective_info(),
                    'result_contract': ResultContract(kind='semantic_cache_hit', exit_reason='semantic_cache_hit'),
                }
            )
        payload.update(
            self._step_progress_update(
                state,
                'lookup_cache',
                exit_reason='semantic_cache_hit' if cached_response is not None else 'cache_miss',
            )
        )
        self._record_node('lookup_cache', state, hit=bool(payload.get('cache_hit')))
        return payload

    def route_cache(self, state: QueryGraphState) -> str:
        """根据缓存命中情况路由。"""

        route = (
            self._next_pending_task_step(state, candidates=['persist_session'])
            if state.get('cache_hit')
            else 'dispatch_query_step'
        )
        return self._record_route(state, route, from_step='lookup_cache')

    def cache_hit_response(self, state: QueryGraphState) -> QueryGraphUpdate:
        """把缓存命中的结果桥接为兼容 SSE 的中间状态。"""

        citations = state.get('citations') or []
        compression_info = state.get('compression_info') or {}
        guardrails = self._guardrails_public(state)
        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_cache_hit_event(events, **(state.get('cache_info') or {}))
            events = append_retrieval_event(
                events,
                retrieved_count=len(citations),
                retrieval_question=(state.get('retrieval_questions') or [''])[0],
                retrieval_questions=(state.get('retrieval_questions') or [])[:6],
                citations=[item.model_dump(mode='json') for item in citations],
                context_compression=compression_info,
                guardrails=guardrails,
            )
            events = append_citation_ready_event(
                events,
                retrieved_count=len(citations),
                citations=self.runtime.stream_citation_snapshot(citations),
            )
            events = append_answer_started_event(
                events,
                retrieved_count=len(citations),
                has_citations=bool(citations),
            )
        self._record_node('cache_hit_response', state, retrieved_count=len(citations))
        return {
            **self._orchestration_route_update(
                self._next_task_step(state, 'lookup_cache', candidates=['persist_session'], default='finalize')
            ),
            'events': events,
        }

    def retrieve_evidence(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行检索并做 citation 脱敏。"""

        request = state['request']
        context_bundle = self._build_query_context_bundle(state, 'retrieve_evidence')
        step_spec = self._step_spec(state, 'retrieve_evidence')
        react_state = self.react_runtime.initialize(step_spec, context_bundle)
        selected_action = self.react_runtime.next_action(react_state, context_bundle)
        evidence_pack: EvidencePack | None = None
        if selected_action in {
            'retrieve_evidence',
            'retrieve_graph_evidence',
            'rag_retrieve_evidence',
            'rag_retrieve_graph_evidence',
        }:
            query = (state.get('retrieval_questions') or [self._question(state)])[0]
            requested_focus_aspects = list(context_bundle.memory_slice.get('missing_aspects') or [])
            evidence_pack = cast(
                EvidencePack,
                self.execution_harness.run_tool(
                    selected_action,
                    {
                        'query': query,
                        'collection_name': request.collection_name,
                        'doc_ids': [],
                        'top_k': request.top_k,
                        'focus_aspects': requested_focus_aspects,
                    },
                    cast(dict[str, Any], state),
                    context_bundle,
                    failure_action='skip_with_gap',
                    fallback_factory=lambda exc: self._build_evidence_gap_fallback(state, requested_focus_aspects, exc),
                ),
            )
            self.react_runtime.observe(
                react_state,
                action=selected_action,
                observation={
                    'evidence_count': len(evidence_pack.evidence_items),
                    'coverage_score': evidence_pack.coverage_score,
                    'missing_aspects': list(evidence_pack.missing_aspects),
                },
                success=bool(evidence_pack.evidence_items),
                stop_reason='success_criteria_satisfied' if evidence_pack.evidence_items else 'max_turns_reached',
            )
            citations = self._citations_from_evidence_pack(evidence_pack)
        elif self._can_use_knowledge_capability(state):
            evidence_pack = self.rag_facade.retrieve_evidence_for_query(
                question=(state.get('retrieval_questions') or [self._question(state)])[0],
                collection_name=request.collection_name,
                top_k=request.top_k,
                use_graph_rag=request.use_graph_rag,
                use_hybrid_retrieval=request.use_hybrid_retrieval,
                use_rerank=request.use_rerank,
                graph_max_hops=request.graph_max_hops,
                trace_context={
                    'mode': state['mode'],
                    'collection_name': request.collection_name,
                    'retrieval_questions': (state.get('retrieval_questions') or [])[:6],
                },
            )
            citations = self._citations_from_evidence_pack(evidence_pack)
        else:
            citations = self.runtime.retrieve_citations(
                request,
                state.get('retrieval_questions') or [self._question(state)],
                self._question(state),
            )
        citations, citation_redaction = self.runtime.sanitize_citations(
            citations,
            request,
            self._stream_mode(state),
        )
        self._append_run_event(
            state,
            'workflow_step_react_completed',
            mode=state['mode'],
            collection_name=request.collection_name,
            task_type=state['task_spec'].task_type,
            task_step_id='retrieve_evidence',
            selected_action=selected_action,
            turn_count=len(react_state.turns),
            stop_reason=react_state.stop_reason,
        )
        self.trace.record(
            'workflow_step_react_completed',
            {
                'workflow': 'langgraph',
                'mode': state['mode'],
                'collection_name': request.collection_name,
                'task_type': state['task_spec'].task_type,
                'task_step_id': 'retrieve_evidence',
                'selected_action': selected_action,
                'turn_count': len(react_state.turns),
                'stop_reason': react_state.stop_reason,
            },
        )
        self._record_node(
            'retrieve_evidence',
            state,
            retrieved_count=len(citations),
            selected_action=selected_action,
            react_turn_count=len(react_state.turns),
            coverage_score=float(evidence_pack.coverage_score) if evidence_pack is not None else None,
        )
        context_bundles = dict(state.get('context_bundles') or {})
        payload: QueryGraphUpdate = {
            'citations': citations,
            'citation_redaction': citation_redaction,
            'context_bundles': context_bundles,
        }
        payload.update(
            self._step_progress_update(
                state,
                'retrieve_evidence',
                exit_reason='completed' if citations else 'no_evidence',
                degraded=not bool(citations),
            )
        )
        payload.update(
            self._checkpoint_update(
                state,
                'retrieve_evidence',
                next_route='dispatch_query_step',
                merged_state=cast(QueryGraphState, {**state, **payload}),
            )
        )
        return payload

    def _can_use_knowledge_capability(self, state: QueryGraphState) -> bool:
        request = state['request']
        return not any(
            [
                request.filters,
                request.permission_scope,
                request.allowed_permissions,
                request.use_multi_query,
                request.use_multi_rewrite,
                request.use_hyde,
                request.use_long_context_reorder,
                request.use_context_compression,
                request.use_parent_chunk_retrieval,
                request.use_question_oriented_index,
            ]
        )

    def _citations_from_evidence_pack(self, evidence_pack) -> list[CitationItem]:
        citations: list[CitationItem] = []
        for item in evidence_pack.evidence_items:
            citations.append(
                CitationItem(
                    chunk_id=item.chunk_id,
                    source=item.source,
                    page=item.page,
                    score=item.support_score,
                    text=item.text,
                )
            )
        return citations

    def compress_context(self, state: QueryGraphState) -> QueryGraphUpdate:
        """准备回答上下文并在流式模式下发出 `retrieval` / `citation_ready` 事件。

        该步骤把检索得到的引用列表转换成回答阶段可直接消费的上下文片段，同时记录压缩信息，供
        trace、最终结果和前端事件展示复用。
        """

        request = state['request']
        citations = state.get('citations') or []
        contexts, compression_info = self.runtime.prepare_answer_context(self._question(state), citations, request)
        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_retrieval_event(
                events,
                retrieved_count=len(citations),
                retrieval_question=(state.get('retrieval_questions') or [''])[0],
                retrieval_questions=(state.get('retrieval_questions') or [])[:6],
                citations=[item.model_dump(mode='json') for item in citations],
                context_compression=compression_info,
                guardrails=self._guardrails_public(state, state.get('citation_redaction')),
            )
            events = append_citation_ready_event(
                events,
                retrieved_count=len(citations),
                citations=self.runtime.stream_citation_snapshot(citations),
            )
        self._record_node('compress_context', state, context_count=len(contexts))
        payload: QueryGraphUpdate = {
            'events': events,
            'contexts': contexts,
            'compression_info': compression_info,
        }
        payload.update(self._step_progress_update(state, 'compress_context', events=events))
        return payload

    def grounded_answer(self, state: QueryGraphState) -> QueryGraphUpdate:
        """生成原始答案，或在无证据时给出兜底回答。

        该节点只负责“第一次生成答案”，不会在这里做最终保守改写决策。Self-RAG 相关的接受、
        重检索或保守降级，统一留给后续 `self_reflect` 处理。
        """

        request = state['request']
        citations = state.get('citations') or []
        events = state.get('events', [])
        if self._is_stream_mode(state) and not state.get('cache_hit'):
            events = append_answer_started_event(
                events,
                retrieved_count=len(citations),
                has_citations=bool(citations),
            )

        if not citations:
            answer = '未找到足够依据来回答该问题，请尝试补充文档、放宽筛选条件或换一种问法。'
            self._record_node('grounded_answer', state, answer_mode='no_context')
            payload: QueryGraphUpdate = {
                'events': events,
                'answer': answer,
                'answer_mode': 'no_context',
                'answer_redaction': self.runtime.empty_redaction_state(
                    self.runtime.use_pii_redaction(request)
                ),
                'corrective_info': self.runtime.empty_corrective_info(),
                'result_contract': ResultContract(
                    kind='no_context',
                    exit_reason='no_context',
                    degraded=True,
                    fallback_action_applied='skip_with_gap',
                ),
            }
            payload.update(
                self._step_progress_update(
                    state,
                    'grounded_answer',
                    events=events,
                    exit_reason='no_context',
                    degraded=True,
                )
            )
            return payload

        guardrail_state = state.get('guardrail_state')
        if guardrail_state is None:
            raise RuntimeError('workflow state missing guardrail_state before grounded_answer')
        prompt_question = guardrail_state['sanitized_question']
        prompt = self.runtime.build_qa_prompt(
            prompt_question,
            state.get('contexts') or [],
            use_guardrails=guardrail_state['prompt_guardrails_enabled'],
        )
        self.trace.record('query_prompt', {'prompt_preview': prompt[:400]})
        prompt_spec = build_ad_hoc_prompt_spec(
            prompt_id='query_grounded_answer',
            purpose='grounded_answer',
            prompt_text=prompt,
        )
        prompt_build_request = build_prompt_build_request(
            prompt_spec=prompt_spec,
            task_spec_ref=state['task_run'].task_id,
            step_spec_ref='grounded_answer',
            context_bundle_ref='grounded_answer',
            tool_specs_ref=[],
        )
        prompt_build_result = build_ad_hoc_prompt_build_result(
            prompt_text=prompt,
            output_contract={'step': 'grounded_answer'},
            build_notes=[f'citation_count:{len(citations)}'],
        )
        raw_answer, raw_answer_mode = self.runtime.generate_answer_with_mode(
            question=prompt_question,
            prompt=prompt,
            citations=citations,
            collection_name=request.collection_name,
        )
        self._record_node('grounded_answer', state, answer_mode=raw_answer_mode)
        payload = {
            'events': events,
            'prompt': prompt,
            'raw_answer': raw_answer,
            'raw_answer_mode': raw_answer_mode,
            'prompt_specs': [*(state.get('prompt_specs') or []), prompt_spec],
            'prompt_build_requests': [*(state.get('prompt_build_requests') or []), prompt_build_request],
            'prompt_build_results': [*(state.get('prompt_build_results') or []), prompt_build_result],
            'graph_subgraph': citations_to_graph_subgraph(citations),
        }
        payload.update(
            self._step_progress_update(
                state,
                'grounded_answer',
                events=events,
                exit_reason=str(raw_answer_mode or 'completed'),
            )
        )
        return payload

    def self_reflect(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行 Corrective RAG 自检与保守重写。

        这个步骤会把原始答案、证据质量和 grounding 结果收敛成结构化 `ReflectionDecision`，
        决定当前答案是可以直接接受、需要保守改写，还是应重新进入检索链路。
        """

        request = state['request']
        citations = state.get('citations') or []
        if not citations:
            self._record_node('self_reflect', state, skipped=True)
            payload: QueryGraphUpdate = {}
            payload.update(
                self._step_progress_update(
                    state,
                    'self_reflect',
                    events=state.get('events', []),
                    exit_reason='skipped_no_citations',
                    skipped=True,
                )
            )
            return payload
        guardrail_state = state.get('guardrail_state')
        if guardrail_state is None:
            raise RuntimeError('workflow state missing guardrail_state before self_reflect')

        answer, answer_mode, corrective_info = self.runtime.maybe_apply_corrective_rag(
            payload=request,
            question=guardrail_state['sanitized_question'],
            answer=state.get('raw_answer') or '',
            answer_mode=state.get('raw_answer_mode') or 'local_fallback',
            citations=citations,
            collection_name=request.collection_name,
        )
        answer, answer_redaction = self.runtime.sanitize_text(
            answer,
            request,
            target='answer',
            trace_context=self._stream_mode(state),
        )
        events = state.get('events', [])
        if self._is_stream_mode(state) and request.use_corrective_rag:
            events = append_corrective_check_event(events, **corrective_info)
        reflection_decision = self._build_reflection_decision(state, corrective_info)
        self._record_node(
            'self_reflect',
            state,
            enabled=bool(corrective_info.get('enabled')),
            applied=bool(corrective_info.get('applied')),
            reflection_decision=reflection_decision.model_dump(mode='json'),
        )
        payload = {
            'events': events,
            'answer': answer,
            'answer_mode': answer_mode,
            'answer_redaction': answer_redaction,
            'corrective_info': corrective_info,
            'self_rag_decision': reflection_decision.decision,
            'retrieval_quality_report': build_retrieval_quality_report(
                query=self._question(state),
                coverage_score=1.0 if citations else 0.0,
                relevance_score=1.0 if citations else 0.0,
                confidence_score=float(reflection_decision.confidence or 0.0),
                suggested_actions=(['retry_retrieve'] if reflection_decision.decision == 'retry_retrieve' else []),
            ),
            'result_contract': ResultContract(
                kind=(
                    'retry_retrieve'
                    if reflection_decision.decision == 'retry_retrieve'
                    else 'corrective_rewrite_applied'
                    if reflection_decision.decision == 'rewrite_answer'
                    else 'grounded_answer'
                ),
                exit_reason=reflection_decision.exit_reason,
                degraded=reflection_decision.decision == 'rewrite_answer',
                fallback_action_applied=reflection_decision.fallback_action,
            ),
        }
        payload.update(
            self._step_progress_update(
                state,
                'self_reflect',
                events=events,
                exit_reason=reflection_decision.exit_reason or reflection_decision.decision,
                fallback_action_applied=reflection_decision.fallback_action,
                degraded=reflection_decision.decision == 'rewrite_answer',
                reflection_decision=reflection_decision,
            )
        )
        payload.update(
            self._checkpoint_update(
                state,
                'self_reflect',
                next_route=(
                    'retry_retrieve'
                    if reflection_decision.decision == 'retry_retrieve'
                    else 'rewrite_answer'
                    if reflection_decision.decision == 'rewrite_answer'
                    else 'dispatch_query_step'
                ),
                merged_state=cast(QueryGraphState, {**state, **payload}),
            )
        )
        return payload

    def route_reflection(self, state: QueryGraphState) -> str:
        """根据自检结果决定是否进入接受、重检索或保守改写分支。

        这里会综合考虑请求开关、当前已重试次数和 reflection 决策本身，避免 Self-RAG 进入无界
        重试。
        """

        request = state['request']
        corrective_info = state.get('corrective_info') or {}
        retry_count = int(state.get('retry_count') or 0)
        max_retry_count = int(state.get('max_retry_count') or 0)
        retry_allowed = self._self_rag_retry_enabled(state) and retry_count < max_retry_count
        reflection_decision = state.get('reflection_decision') or self._build_reflection_decision(state, corrective_info)
        if request.use_corrective_rag:
            self.trace.record(
                'self_rag_decision',
                {
                    'workflow': 'langgraph',
                    'mode': state['mode'],
                    'collection_name': request.collection_name,
                    'decision': reflection_decision.decision,
                    'supported': reflection_decision.supported,
                    'risk': reflection_decision.risk,
                    'confidence': reflection_decision.confidence,
                    'final_mode': reflection_decision.final_mode,
                    'reason': reflection_decision.reason,
                    'fallback_action': reflection_decision.fallback_action,
                    'retry_count': retry_count,
                    'max_retry_count': max_retry_count,
                    'retry_allowed': retry_allowed,
                },
            )
        if reflection_decision.decision == 'retry_retrieve':
            return self._record_route(state, 'retry_retrieve', from_step='self_reflect')
        if reflection_decision.decision == 'rewrite_answer':
            return self._record_route(state, 'rewrite_answer', from_step='self_reflect')
        return self._record_route(state, 'dispatch_query_step', from_step='self_reflect')

    def rewrite_answer(self, state: QueryGraphState) -> QueryGraphUpdate:
        """保留保守重写节点，便于后续扩展更细粒度的改写流。

        当前实现里，真正的保守改写已在 `self_reflect` 内完成；这里更像一个显式占位节点，用于
        在图结构和 trace 层表达“答案进入了保守降级分支”。
        """

        self._record_node('rewrite_answer', state, answer_mode=state.get('answer_mode'))
        return {
            **self._orchestration_route_update('dispatch_query_step'),
            'current_step_id': 'self_reflect',
        }

    def retry_retrieve(self, state: QueryGraphState) -> QueryGraphUpdate:
        """准备一次基于反思结果的重检索。

        该节点不会直接发起检索，而是只负责递增重试计数、生成新一轮检索问题，并把流程重新路由
        回 `retrieve_evidence`。
        """

        retry_count = int(state.get('retry_count') or 0) + 1
        retry_questions = self._build_retry_questions(state)
        corrective_info = state.get('corrective_info') or {}
        retry_reason = str(corrective_info.get('reason') or 'low_grounding_confidence')
        self._record_node(
            'retry_retrieve',
            state,
            retry_count=retry_count,
            retry_reason=retry_reason,
            query_count=len(retry_questions),
        )
        return {
            **self._orchestration_route_update('retrieve_evidence'),
            'retry_count': retry_count,
            'retrieval_questions': retry_questions,
            'self_rag_decision': 'retry_retrieve',
            'retry_reason': retry_reason,
        }

    def persist_session(self, state: QueryGraphState) -> QueryGraphUpdate:
        """把当前轮次结果持久化到会话消息，并触发摘要刷新。

        只有 chat 模式会执行到这里。节点内部还会做一次幂等检查，避免在重放、恢复或重复调用时
        把同一轮问答写入两次。
        """

        request = cast(ChatRequest, state['request'])
        if not hasattr(self.runtime, 'get_or_create_session'):
            self._record_node('persist_session', state, skipped=True)
            payload: QueryGraphUpdate = {}
            payload.update(
                self._step_progress_update(
                    state,
                    'persist_session',
                    exit_reason='session_persist_skipped',
                    skipped=True,
                    degraded=True,
                )
            )
            return payload

        guardrail_state = state.get('guardrail_state')
        stored_question = request.question.strip()
        if guardrail_state is not None:
            stored_question = self.runtime.question_for_storage(request.question.strip(), guardrail_state)
        answer = state.get('answer') or ''
        session = self.runtime.get_or_create_session(request.session_id)
        messages = session.get('messages', [])
        already_persisted = (
            len(messages) >= 2
            and messages[-2].get('role') == 'user'
            and messages[-2].get('content') == stored_question
            and messages[-1].get('role') == 'assistant'
            and messages[-1].get('content') == answer
        )
        if not already_persisted:
            session['messages'].append(self.runtime.message('user', stored_question))
            session['messages'].append(self.runtime.message('assistant', answer))
            session['updated_at'] = datetime.now(timezone.utc)
            self.runtime.save_session(request.session_id)
            self.runtime.auto_summarize_session(request.session_id)
        self._record_node('persist_session', state, skipped=False, session_id=request.session_id)
        payload = {
            'metadata': {
                **state.get('metadata', {}),
                'session_persisted': True,
            }
        }
        payload.update(self._step_progress_update(state, 'persist_session'))
        return payload

    def finalize(self, state: QueryGraphState) -> QueryGraphUpdate:
        """产出最终响应、补齐流式尾事件，并写入 trace/cache。

        该节点负责把前面分散在多个节点中的中间状态统一收束成最终 `QueryResponse`，
        同时补写缓存、trace 和 SSE 完结事件。
        """

        request = state['request']
        citations = state.get('citations') or []
        answer = state.get('answer') or ''
        answer_mode = state.get('answer_mode') or 'no_context'
        corrective_info = state.get('corrective_info') or self.runtime.empty_corrective_info()
        compression_info = state.get('compression_info') or {
            'enabled': False,
            'original_chunk_count': len(citations),
            'compressed_chunk_count': len(citations),
            'original_sentence_count': 0,
            'compressed_sentence_count': 0,
            'original_char_count': 0,
            'compressed_char_count': 0,
            'strategy': 'disabled',
        }
        citation_redaction = state.get('citation_redaction')
        answer_redaction = state.get('answer_redaction')
        latency_ms = int((perf_counter() - state.get('started_at', perf_counter())) * 1000)
        result_contract = dump_result_contract(state.get('result_contract')) or {}
        grounded_context = None
        evidence_pack = state.get('evidence_pack')
        if evidence_pack is not None:
            grounded_context = evidence_pack_to_grounded_context(
                objective=state['task_spec'].objective,
                evidence_pack=evidence_pack,
                evidence_pack_ref=f'{state["task_run"].run_id}:evidence_pack',
                unresolved_gaps=list((state.get('corrective_info') or {}).get('missing_aspects') or []),
            )
        result_artifact = QueryResultArtifact(
            artifact_id=f'qart-{uuid4().hex[:12]}',
            artifact_type='query_answer_artifact',
            created_at=datetime.now(timezone.utc),
            content=QueryResultArtifactContent(
                answer=answer,
                answer_mode=answer_mode,
                citations=list(citations),
                grounded=bool(citations),
                degraded=bool(result_contract.get('degraded')),
                session_id=request.session_id,
                retrieval_questions=[item for item in (state.get('retrieval_questions') or []) if item],
                metadata={
                    'collection_name': request.collection_name,
                    'task_type': state['task_spec'].task_type,
                    'exit_reason': result_contract.get('exit_reason'),
                    'fallback_action_applied': result_contract.get('fallback_action_applied'),
                },
            ),
        )
        result_contract = ResultContract.model_validate(
            {
                **result_contract,
                'artifact_type': result_artifact.artifact_type,
                'result_artifact_id': result_artifact.artifact_id,
                'result_artifact_type': result_artifact.artifact_type,
            }
        )
        response = QueryResponse(
            answer=answer,
            citations=citations,
            retrieved_count=len(citations),
            latency_ms=latency_ms,
            session_id=request.session_id,
            result_artifact=result_artifact,
        )

        # 仅在非护栏拦截且非缓存命中的情况下写入缓存，避免无意义覆盖。
        if answer_mode != 'guardrail_blocked' and not state.get('cache_hit'):
            self.runtime.store_semantic_cache(
                request,
                question=state.get('cache_question') or self._question(state),
                cache_mode=self._cache_mode(state),
                response=response,
                answer_mode=answer_mode,
                metadata={
                    'retrieval_questions': (state.get('retrieval_questions') or [])[:6],
                    'corrective_rag': corrective_info,
                    'context_compression': compression_info,
                    'stream_mode': state['mode'] if self._is_stream_mode(state) else None,
                },
            )

        trace_payload: dict[str, Any] = {
            'workflow': 'langgraph',
            'collection_name': request.collection_name,
            'task_type': state['task_spec'].task_type,
            'completed_step_ids': list(state.get('completed_step_ids') or []),
            'retrieved_count': len(citations),
            'latency_ms': latency_ms,
            'answer_mode': answer_mode,
            'corrective_rag': corrective_info,
            'guardrails': self._guardrails_public(state, citation_redaction, answer_redaction),
            'cache_hit': bool(state.get('cache_hit')),
            'retry_count': int(state.get('retry_count') or 0),
            'self_rag_decision': state.get('self_rag_decision'),
            'reflection_decision': (
                state['reflection_decision'].model_dump(mode='json')
                if state.get('reflection_decision') is not None
                else None
            ),
            'result_contract': result_contract,
            'grounded_context': grounded_context,
            'task_run': state['task_run'].model_dump(mode='json'),
        }
        if answer_mode != 'guardrail_blocked':
            trace_payload.update(
                {
                    'retrieval_question': ((state.get('retrieval_questions') or [''])[:1] or [''])[0][:200],
                    'retrieval_questions': [item[:200] for item in (state.get('retrieval_questions') or [])[:6]],
                    'use_hybrid_retrieval': request.use_hybrid_retrieval,
                    'use_multi_query': request.use_multi_query,
                    'multi_query': state.get('multi_query_info'),
                    'use_multi_rewrite': request.use_multi_rewrite,
                    'multi_rewrite': state.get('multi_rewrite_info'),
                    'use_hyde': request.use_hyde,
                    'hyde': state.get('hyde_info'),
                    'use_long_context_reorder': request.use_long_context_reorder,
                    'use_parent_chunk_retrieval': request.use_parent_chunk_retrieval,
                    'use_question_oriented_index': request.use_question_oriented_index,
                    'use_corrective_rag': request.use_corrective_rag,
                    **self.runtime.graph_trace_flags(request),
                    'use_context_compression': compression_info.get('enabled', False),
                    'context_compression': compression_info,
                    'semantic_cache': state.get('cache_info'),
                }
            )
            if self._is_stream_mode(state):
                trace_payload['stream_mode'] = state['mode']
        self._append_run_event(state, 'query_completed', **trace_payload)
        self.trace.record('query_completed', trace_payload)

        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_delta_events(events, self.runtime.chunk_text_for_stream(answer))
            events = append_answer_completed_event(
                events,
                answer_length=len(answer),
                answer_mode=answer_mode,
                retrieved_count=len(citations),
                corrective_rag=corrective_info,
                guardrails=self._guardrails_public(state, citation_redaction, answer_redaction),
            )
            events = append_done_event(events, response)

        self._record_node('finalize', state, answer_mode=answer_mode)
        return {
            'events': events,
            'grounded_context': grounded_context,
            'result_contract': result_contract,
            'result': response,
        }

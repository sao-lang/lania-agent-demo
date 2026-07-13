"""RAG 系统查询工作流节点模块。

定义 LangGraph 查询工作流中的各个节点，把 ``RagQueryEngine`` 的线性查询链路
拆解成更细粒度的状态转换步骤，在图编排中插入缓存、反思、自愈重试和流式事件逻辑。

与 ``app/workflows/query_nodes.py`` 功能一致，但**不依赖主应用的 harness 基础设施**
（ExecutionHarness/ContextHarness/ReActRuntime/ReflectionHarness/ToolRegistry 等），
所有 RAG 操作通过 ``RagGraphRuntime`` 协议委派给 rag_system 的组件。
"""

from __future__ import annotations

from time import perf_counter, time
from typing import Any, cast
from uuid import uuid4

from app.rag_system.knowledge.base import (
    EvidencePack,
    GroundedAnswerRequest,
    KnowledgeSearchRequest,
)
from app.rag_system.knowledge.contracts import RetrievalQualityReport
from app.rag_system.knowledge.service import RagKnowledgeCapability
from app.rag_system.models.query import (
    ChatRequest,
    CitationItem,
    QueryRequest,
    QueryResponse,
)
from app.rag_system.query.graph.events import (
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
from app.rag_system.query.graph.runtime import RagGraphRuntime
from app.rag_system.query.graph.state import (
    QUERY_GRAPH_ENTRY_ROUTES,
    QueryGraphState,
    QueryGraphUpdate,
)
from app.rag_system.query.graph.step_lifecycle import (
    create_checkpoint_record,
    create_run_event,
    mark_step_completed,
    mark_step_failed,
    mark_step_started,
)

# 步骤节点（step）：有进程生命周期，执行实际业务逻辑。
STEP_NODE_IDS = frozenset({
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
})
# 编排节点（orchestration）：无业务逻辑，仅做路由/状态转换。
ORCHESTRATION_NODE_IDS = frozenset({
    'dispatch_query_step',
    'blocked_response',
    'cache_hit_response',
    'retry_retrieve',
    'rewrite_answer',
    'finalize',
})
# 需要创建 checkpoint 的步骤。
CHECKPOINT_STEP_IDS = frozenset({'check_guardrails', 'retrieve_evidence', 'self_reflect'})


class RagQueryGraphNodes:
    """封装 RAG 查询工作流各节点实现。

    把查询链路拆成一组可被 LangGraph 调度的细粒度步骤：输入护栏、会话上下文装载、
    问题改写、多路检索、缓存复用、证据整理、答案生成、自反思以及会话持久化。

    与 ``app/workflows/query_nodes.QueryWorkflowNodes`` 的区别：
    - 不依赖 ``ExecutionHarness``/``ContextHarness``/``ReActRuntime``/``ReflectionHarness``
    - 所有 RAG 操作通过 ``self.runtime`` 协议委派
    - 检索路径仅保留直接调 RAG 引擎的方式（无 ReAct 工具选择）
    """

    def __init__(
        self,
        runtime: RagGraphRuntime,
        trace: Any | None = None,
        knowledge_capability: RagKnowledgeCapability | None = None,
    ) -> None:
        """初始化工作流节点集合。

        Args:
            runtime: 符合 ``RagGraphRuntime`` 协议的 RAG 引擎实例。
            trace: 可选的追踪记录器。
            knowledge_capability: 可选的知识能力实例；默认从 runtime 获取。
        """
        self.runtime = runtime
        self.trace = trace
        self.knowledge_capability = (
            knowledge_capability
            or getattr(runtime, 'knowledge_capability', None)
        )

    # ── 辅助方法 ───────────────────────────────────────────

    def _is_stream_mode(self, state: QueryGraphState) -> bool:
        return state['mode'] in {'query_stream', 'chat_stream'}

    def _cache_mode(self, state: QueryGraphState) -> str:
        if state['mode'] in {'query', 'query_stream'}:
            return 'query'
        return 'chat_stream' if state['mode'] == 'chat_stream' else 'chat'

    def _stream_mode(self, state: QueryGraphState) -> str:
        return state['mode']

    def _question(self, state: QueryGraphState) -> str:
        return state['request'].question.strip()

    def _node_kind(self, name: str) -> str:
        if name in STEP_NODE_IDS:
            return 'step'
        if name in ORCHESTRATION_NODE_IDS:
            return 'orchestration'
        return 'runtime'

    def _record_node(self, name: str, state: QueryGraphState, **extra: Any) -> None:
        """记录节点执行到 trace。"""
        if self.trace is not None:
            self.trace.record(f'graph_node_{name}', {
                'mode': state['mode'],
                'kind': self._node_kind(name),
                **extra,
            })

    def _citations_from_evidence_items(self, items: list[dict[str, Any]]) -> list[CitationItem]:
        """将 EvidencePack items 转换为 CitationItem 列表。"""
        from app.rag_system.models.query import CitationItem
        citations = []
        for item in items:
            citations.append(CitationItem(
                chunk_id=item.get('chunk_id', ''),
                source=item.get('source', ''),
                file_path=item.get('file_path'),
                page=item.get('page'),
                score=item.get('score'),
                text=item.get('text', ''),
                section_title=item.get('section_title'),
            ))
        return citations

    def _step_progress_update(
        self,
        state: QueryGraphState,
        step_id: str,
        *,
        events: list | None = None,
        exit_reason: str = 'completed',
        fallback_action_applied: str | None = None,
        degraded: bool = False,
        skipped: bool = False,
        error: str | None = None,
    ) -> QueryGraphUpdate:
        """构造步骤进度更新字典。"""
        task_run = state['task_run']
        completed = state.get('completed_step_ids', [])
        current_events = list(events) if events is not None else list(state.get('events', []))

        if error:
            runtime = mark_step_failed(task_run, step_id, exit_reason=exit_reason)
            if self._is_stream_mode(state):
                current_events = append_step_failed_event(
                    current_events,
                    step_id=step_id,
                    exit_reason=exit_reason,
                    error=error,
                )
        elif skipped:
            runtime = mark_step_completed(
                task_run, step_id, completed_step_ids=completed,
                exit_reason=exit_reason, skipped=True,
            )
            if self._is_stream_mode(state):
                current_events = append_step_completed_event(
                    current_events,
                    step_id=step_id,
                    exit_reason=exit_reason,
                    skipped=True,
                )
        else:
            runtime = mark_step_completed(
                task_run, step_id, completed_step_ids=completed,
                exit_reason=exit_reason,
                fallback_action_applied=fallback_action_applied,
                degraded=degraded,
            )
            if self._is_stream_mode(state):
                current_events = append_step_completed_event(
                    current_events,
                    step_id=step_id,
                    exit_reason=exit_reason,
                    degraded=degraded,
                )

        update: QueryGraphUpdate = {
            'task_run': task_run,
            'completed_step_ids': completed,
            'events': current_events,
        }
        if step_id in CHECKPOINT_STEP_IDS:
            cp = create_checkpoint_record(step_id, len(completed), dict(state))
            checkpoints = list(state.get('checkpoints', [])) + [cp]
            update['checkpoints'] = checkpoints
            if self._is_stream_mode(state):
                update['events'] = append_checkpoint_created_event(
                    current_events,
                    checkpoint_id=cp['checkpoint_id'],
                    step_id=step_id,
                )

        return update

    def validate_node_contract(self, node_name: str, update: QueryGraphUpdate) -> None:
        """验证节点返回值契约。"""
        assert isinstance(update, dict), f'{node_name} 必须返回 dict'

    def handle_node_exception(self, node_name: str, state: QueryGraphState, exc: Exception) -> None:
        """在节点异常时保存部分状态。"""
        if self.trace is not None:
            self.trace.record(f'graph_node_error_{node_name}', {
                'error': str(exc),
                'mode': state.get('mode'),
            })

    # ── 路由函数 ───────────────────────────────────────────

    def route_guardrail(self, state: QueryGraphState) -> str:
        """根据护栏状态路由。"""
        guardrail = state.get('guardrail_state', {})
        if guardrail.get('blocked'):
            return 'blocked_response'
        return 'dispatch_query_step'

    def route_query_step(self, state: QueryGraphState) -> str:
        """按任务规格的步骤顺序分发。"""
        task_run = state.get('task_run', {})
        completed = state.get('completed_step_ids', [])
        task_spec = state.get('task_spec', {})
        steps = task_spec.get('steps', [])

        for step in steps:
            sid = step.get('step_id', '')
            if sid not in completed and sid != state.get('current_step_id'):
                return sid
        return 'finalize'

    def route_cache(self, state: QueryGraphState) -> str:
        """根据缓存命中情况路由。"""
        if state.get('cache_hit'):
            return 'cache_hit_response'
        next_step = self.route_query_step(state)
        if next_step == 'finalize':
            return 'finalize'
        return 'dispatch_query_step'

    def route_reflection(self, state: QueryGraphState) -> str:
        """根据 Self-RAG 反思结果路由。"""
        citations = state.get('citations') or []
        if not citations:
            return 'dispatch_query_step'

        corrective_info = state.get('corrective_info') or {}
        decision = corrective_info.get('decision', 'accept')
        if decision == 'retry':
            return 'retry_retrieve'
        elif decision == 'rewrite':
            return 'rewrite_answer'
        return 'dispatch_query_step'

    def route_orchestration(self, state: QueryGraphState, from_step: str) -> str:
        """通用编排路由：完成后回到分发或结束。"""
        next_step = self.route_query_step(state)
        if next_step == 'finalize':
            return 'finalize'
        return 'persist_session'

    # ── 节点 1: check_guardrails ──────────────────────────

    def check_guardrails(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行输入护栏检查，初始化本轮 query 的基础运行态。"""
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
                use_query_rewrite=getattr(request, 'use_query_rewrite', False),
                use_multi_query=getattr(request, 'use_multi_query', False),
                multi_query_count=getattr(request, 'multi_query_count', 3),
                use_multi_rewrite=getattr(request, 'use_multi_rewrite', False),
                multi_rewrite_count=getattr(request, 'multi_rewrite_count', 3),
                use_hybrid_retrieval=getattr(request, 'use_hybrid_retrieval', False),
                use_rerank=getattr(request, 'use_rerank', True),
                use_hyde=getattr(request, 'use_hyde', False),
                use_long_context_reorder=getattr(request, 'use_long_context_reorder', False),
                use_parent_chunk_retrieval=getattr(request, 'use_parent_chunk_retrieval', False),
                use_question_oriented_index=getattr(request, 'use_question_oriented_index', False),
                use_corrective_rag=getattr(request, 'use_corrective_rag', False),
                use_context_compression=self.runtime.use_context_compression(request),
                use_pii_redaction=self.runtime.use_pii_redaction(request),
            )

        task_run = state['task_run']
        mark_step_started(task_run, 'check_guardrails')
        self._record_node('check_guardrails', state, blocked=guardrail_state.get('blocked', False))

        payload: QueryGraphUpdate = {
            'guardrail_state': guardrail_state,
            'events': events,
            'task_run': task_run,
        }
        payload.update(self._step_progress_update(
            state, 'check_guardrails',
            events=events,
            exit_reason='guardrail_blocked' if guardrail_state.get('blocked') else 'passed',
        ))
        return payload

    # ── 节点 2: dispatch_query_step ───────────────────────

    def dispatch_query_step(self, state: QueryGraphState) -> QueryGraphUpdate:
        """空节点：仅做分发路由，不产生副作用。"""
        return {}

    # ── 节点 3: blocked_response ──────────────────────────

    def blocked_response(self, state: QueryGraphState) -> QueryGraphUpdate:
        """生成护栏拦截响应。"""
        request = state['request']
        guardrail_state = state.get('guardrail_state', {})
        blocked_response = self.runtime.build_blocked_query_response(request, guardrail_state)
        events = state.get('events', [])
        if self._is_stream_mode(state):
            events = append_done_event(events, blocked_response)

        self._record_node('blocked_response', state)
        return {
            'result': blocked_response,
            'events': events,
        }

    # ── 节点 4: load_session_context ──────────────────────

    def load_session_context(self, state: QueryGraphState) -> QueryGraphUpdate:
        """加载会话上下文（仅 chat 模式）。"""
        request = state['request']
        if not isinstance(request, ChatRequest) or not request.session_id:
            return self._step_progress_update(
                state, 'load_session_context',
                exit_reason='skipped_no_session', skipped=True,
            )

        session = self.runtime.load_session(request.session_id)
        events = state.get('events', [])
        self._record_node('load_session_context', state, has_session=session is not None)

        payload: QueryGraphUpdate = {
            'metadata': {**state.get('metadata', {}), 'session_loaded': session is not None},
            'events': events,
        }
        payload.update(self._step_progress_update(
            state, 'load_session_context',
            events=events,
            exit_reason='loaded' if session else 'not_found',
        ))
        return payload

    # ── 节点 5: rewrite_query ─────────────────────────────

    def rewrite_query(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行问题改写。"""
        request = state['request']
        question = self._question(state)
        use_rewrite = getattr(request, 'use_query_rewrite', True)
        retrieval_question, rewrite_info = self.runtime.resolve_rewrite_info(
            question, use_rewrite, self._stream_mode(state),
        )
        events = state.get('events', [])
        if self._is_stream_mode(state) and rewrite_info:
            events = append_rewrite_event(events, **rewrite_info)

        self._record_node('rewrite_query', state, applied=rewrite_info is not None)
        payload: QueryGraphUpdate = {
            'rewrite_info': rewrite_info,
            'retrieval_questions': [retrieval_question],
            'events': events,
        }
        payload.update(self._step_progress_update(
            state, 'rewrite_query', events=events,
            exit_reason='applied' if rewrite_info else 'skipped',
            skipped=rewrite_info is None,
        ))
        return payload

    # ── 节点 6: expand_queries ────────────────────────────

    def expand_queries(self, state: QueryGraphState) -> QueryGraphUpdate:
        """展开多路查询（多改写、多查询、HyDE）。"""
        request = state['request']
        retrieval_question = (state.get('retrieval_questions') or [self._question(state)])[0]
        answer_question = self._question(state)
        events = state.get('events', [])
        flags: dict[str, Any] = {}

        # 多改写
        if getattr(request, 'use_multi_rewrite', False):
            questions, info = self.runtime.maybe_apply_multi_rewrite(
                request, retrieval_question, answer_question, self._stream_mode(state),
            )
            if info:
                flags['multi_rewrite_info'] = info
                flags['retrieval_questions'] = questions
                if self._is_stream_mode(state):
                    events = append_multi_rewrite_event(events, **info)

        # 多查询
        if getattr(request, 'use_multi_query', False):
            questions, info = self.runtime.maybe_apply_multi_query(
                request, retrieval_question, answer_question, self._stream_mode(state),
            )
            if info:
                flags['multi_query_info'] = info
                flags['retrieval_questions'] = questions
                if self._is_stream_mode(state):
                    events = append_multi_query_event(events, **info)

        # HyDE
        if getattr(request, 'use_hyde', False):
            hyde_question, info = self.runtime.maybe_apply_hyde(
                request, retrieval_question, answer_question, self._stream_mode(state),
            )
            if info:
                flags['hyde_info'] = info
                flags['retrieval_questions'] = [hyde_question]
                if self._is_stream_mode(state):
                    events = append_hyde_event(events, **info)

        self._record_node('expand_queries', state, **{
            k: bool(v) for k, v in flags.items()
        })
        payload: QueryGraphUpdate = {**flags, 'events': events}
        payload.update(self._step_progress_update(
            state, 'expand_queries', events=events,
            exit_reason='expanded' if flags else 'skipped',
            skipped=not flags,
        ))
        return payload

    # ── 节点 7: lookup_cache ──────────────────────────────

    def lookup_cache(self, state: QueryGraphState) -> QueryGraphUpdate:
        """查找语义缓存。命中时桥接缓存结果到工作流状态。"""
        request = state['request']
        cache_question = state.get('cache_question') or self._question(state)
        cached_response, cache_info = self.runtime.lookup_semantic_cache(
            request, cache_question, self._cache_mode(state),
        )
        payload: QueryGraphUpdate = {
            'cache_info': cache_info,
            'cache_hit': cached_response is not None,
        }

        if cached_response is not None:
            contexts, compression_info = self.runtime.prepare_answer_context(
                self._question(state), cached_response.citations, request,
            )
            payload.update({
                'citations': cached_response.citations,
                'citation_redaction': self.runtime.empty_redaction_state(
                    self.runtime.use_pii_redaction(request),
                ),
                'contexts': contexts,
                'compression_info': compression_info,
                'answer': cached_response.answer,
                'raw_answer': cached_response.answer,
                'raw_answer_mode': 'semantic_cache_hit',
                'corrective_info': self.runtime.empty_corrective_info(),
            })

        self._record_node('lookup_cache', state, hit=bool(payload.get('cache_hit')))
        payload.update(self._step_progress_update(
            state, 'lookup_cache',
            exit_reason='semantic_cache_hit' if cached_response is not None else 'cache_miss',
        ))
        return payload

    # ── 节点 8: cache_hit_response ────────────────────────

    def cache_hit_response(self, state: QueryGraphState) -> QueryGraphUpdate:
        """缓存命中响应：发出事件并构造最终结果。"""
        request = state['request']
        answer = state.get('answer', '')
        citations = state.get('citations', [])
        events = state.get('events', [])

        if self._is_stream_mode(state):
            events = append_cache_hit_event(events, citations_count=len(citations))
            events = append_delta_events(events, [answer])
            events = append_answer_completed_event(events, mode='semantic_cache_hit')

        result = QueryResponse(
            answer=answer,
            citations=citations,
            retrieved_count=len(citations),
            session_id=getattr(request, 'session_id', None),
            answer_mode='semantic_cache_hit',
        )
        if self._is_stream_mode(state):
            events = append_done_event(events, result)

        self._record_node('cache_hit_response', state)
        return {
            'result': result,
            'events': events,
        }

    # ── 节点 9: retrieve_evidence ─────────────────────────

    def retrieve_evidence(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行证据检索。

        此版本不使用 ExecutionHarness/ReAct 运行时，直接通过
        ``RagGraphRuntime`` 或 ``RagKnowledgeCapability`` 执行检索。
        """
        request = state['request']
        retrieval_questions = state.get('retrieval_questions') or [self._question(state)]
        answer_question = self._question(state)
        events = state.get('events', [])

        task_run = state['task_run']
        mark_step_started(task_run, 'retrieve_evidence')

        # 优先使用 knowledge_capability（支持 graph/hybrid/rerank）
        if self.knowledge_capability is not None:
            search_request = KnowledgeSearchRequest(
                query=retrieval_questions[0],
                collection_name=request.collection_name,
                top_k=getattr(request, 'top_k', 5),
                use_graph_rag=getattr(request, 'use_graph_rag', False),
                use_hybrid_retrieval=getattr(request, 'use_hybrid_retrieval', False),
                use_rerank=getattr(request, 'use_rerank', True),
                graph_max_hops=getattr(request, 'graph_max_hops', 1),
            )
            evidence_pack = self.knowledge_capability.retrieve_evidence(search_request)
            citations = self._citations_from_evidence_items(evidence_pack.items)
        else:
            # 兜底：直接通过 runtime 检索
            citations = self.runtime.retrieve_citations(
                request, retrieval_questions, answer_question,
            )

        citations, citation_redaction = self.runtime.sanitize_citations(
            citations, request, self._stream_mode(state),
        )

        if self._is_stream_mode(state):
            events = append_retrieval_event(
                events,
                retrieval_count=len(retrieval_questions),
                retrieved_count=len(citations),
                document_retrieved=True,
            )
            snapshot = self.runtime.stream_citation_snapshot(citations)
            if snapshot:
                events = append_citation_ready_event(events, citations=snapshot)

        self._record_node('retrieve_evidence', state,
                          retrieval_questions=len(retrieval_questions),
                          citations=len(citations))

        payload: QueryGraphUpdate = {
            'citations': citations,
            'citation_redaction': citation_redaction,
            'events': events,
            'task_run': task_run,
        }
        payload.update(self._step_progress_update(
            state, 'retrieve_evidence', events=events,
            exit_reason='retrieved' if citations else 'no_results',
            skipped=not citations,
        ))
        return payload

    # ── 节点 10: compress_context ─────────────────────────

    def compress_context(self, state: QueryGraphState) -> QueryGraphUpdate:
        """压缩检索上下文。"""
        request = state['request']
        question = self._question(state)
        citations = state.get('citations') or []

        if not citations:
            return self._step_progress_update(
                state, 'compress_context',
                exit_reason='skipped_no_citations', skipped=True,
            )

        contexts, compression_info = self.runtime.prepare_answer_context(
            question, citations, request,
        )
        self._record_node('compress_context', state,
                          original=compression_info.get('original_count'),
                          compressed=compression_info.get('compressed_count'))
        payload: QueryGraphUpdate = {
            'contexts': contexts,
            'compression_info': compression_info,
        }
        payload.update(self._step_progress_update(
            state, 'compress_context',
            exit_reason='compressed' if compression_info.get('compressed') else 'no_compression',
        ))
        return payload

    # ── 节点 11: grounded_answer ──────────────────────────

    def grounded_answer(self, state: QueryGraphState) -> QueryGraphUpdate:
        """生成原始答案。

        该节点只负责"第一次生成答案"，不做 Self-RAG 反思。
        Self-RAG 统一交给后续 ``self_reflect`` 节点处理。
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
                'raw_answer': answer,
                'raw_answer_mode': 'no_context',
                'answer_redaction': self.runtime.empty_redaction_state(
                    self.runtime.use_pii_redaction(request),
                ),
                'corrective_info': self.runtime.empty_corrective_info(),
            }
            payload.update(self._step_progress_update(
                state, 'grounded_answer', events=events,
                exit_reason='no_context', degraded=True,
            ))
            return payload

        guardrail_state = state.get('guardrail_state')
        if guardrail_state is None:
            raise RuntimeError('workflow state missing guardrail_state before grounded_answer')

        prompt_question = guardrail_state['sanitized_question']
        prompt = self.runtime.build_qa_prompt(
            prompt_question,
            state.get('contexts') or [],
            use_guardrails=guardrail_state.get('prompt_guardrails_enabled', True),
        )
        if self.trace is not None:
            self.trace.record('query_prompt', {'prompt_preview': prompt[:400]})

        answer = self.runtime.generate_answer(prompt, stream=self._is_stream_mode(state))

        if isinstance(answer, list):
            # 流式模式：answer 是 delta 列表
            deltas = answer
            full_answer = ''.join(deltas)
            if self._is_stream_mode(state):
                events = append_delta_events(events, deltas)
        else:
            full_answer = answer
            if self._is_stream_mode(state):
                events = append_delta_events(events, [full_answer])

        answer, answer_redaction = self.runtime.sanitize_text(
            full_answer, request, target='answer', trace_context=self._stream_mode(state),
        )
        if self._is_stream_mode(state):
            events = append_answer_completed_event(
                events, mode='grounded' if citations else 'no_context',
            )

        self._record_node('grounded_answer', state, answer_mode='grounded')
        payload: QueryGraphUpdate = {
            'events': events,
            'answer': answer,
            'raw_answer': full_answer,
            'raw_answer_mode': 'grounded',
            'answer_redaction': answer_redaction,
            'corrective_info': self.runtime.empty_corrective_info(),
        }
        payload.update(self._step_progress_update(
            state, 'grounded_answer', events=events,
            exit_reason='generated',
        ))
        return payload

    # ── 节点 12: self_reflect ─────────────────────────────

    def self_reflect(self, state: QueryGraphState) -> QueryGraphUpdate:
        """执行 Corrective RAG 自检与保守重写。"""
        request = state['request']
        citations = state.get('citations') or []

        if not citations:
            self._record_node('self_reflect', state, skipped=True)
            payload: QueryGraphUpdate = {}
            payload.update(self._step_progress_update(
                state, 'self_reflect',
                events=state.get('events', []),
                exit_reason='skipped_no_citations', skipped=True,
            ))
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
            answer, request, target='answer', trace_context=self._stream_mode(state),
        )
        events = state.get('events', [])
        if self._is_stream_mode(state) and getattr(request, 'use_corrective_rag', False):
            events = append_corrective_check_event(events, **corrective_info)

        self._record_node('self_reflect', state,
                          enabled=bool(corrective_info.get('enabled')),
                          applied=bool(corrective_info.get('applied')))
        payload: QueryGraphUpdate = {
            'events': events,
            'answer': answer,
            'corrective_info': corrective_info,
        }
        payload.update(self._step_progress_update(
            state, 'self_reflect', events=events,
            exit_reason=corrective_info.get('decision', 'accept'),
        ))
        return payload

    # ── 节点 13: retry_retrieve ───────────────────────────

    def retry_retrieve(self, state: QueryGraphState) -> QueryGraphUpdate:
        """重试检索：重置检索状态并重新分发。"""
        self._record_node('retry_retrieve', state)
        return {
            'retrieval_questions': state.get('retrieval_questions', []),
            'orchestration_next_route': 'retrieve_evidence',
        }

    # ── 节点 14: rewrite_answer ───────────────────────────

    def rewrite_answer(self, state: QueryGraphState) -> QueryGraphUpdate:
        """改写答案：保留修正后的答案，继续分发。"""
        self._record_node('rewrite_answer', state)
        return {
            'orchestration_next_route': 'dispatch_query_step',
        }

    # ── 节点 15: persist_session ──────────────────────────

    def persist_session(self, state: QueryGraphState) -> QueryGraphUpdate:
        """持久化会话（仅 chat 模式）。"""
        request = state['request']
        if not isinstance(request, (ChatRequest,)) or not request.session_id:
            return self._step_progress_update(
                state, 'persist_session',
                exit_reason='skipped_no_session', skipped=True,
            )

        self.runtime.save_session(request.session_id)
        self._record_node('persist_session', state)
        return self._step_progress_update(
            state, 'persist_session', events=state.get('events', []),
            exit_reason='saved',
        )

    # ── 节点 16: finalize ─────────────────────────────────

    def finalize(self, state: QueryGraphState) -> QueryGraphUpdate:
        """最终化：构造最终 QueryResponse。"""
        request = state['request']
        answer = state.get('answer') or '未生成回答。'
        citations = state.get('citations') or []
        events = state.get('events', [])

        result = QueryResponse(
            answer=answer,
            citations=citations,
            retrieved_count=len(citations),
            session_id=getattr(request, 'session_id', None),
            answer_mode=state.get('raw_answer_mode'),
            degraded=any(
                rt.get('degraded', False)
                for rt in state.get('task_run', {}).get('step_runtimes', {}).values()
            ),
            retrieval_questions=state.get('retrieval_questions', []),
            metadata=state.get('metadata', {}),
        )

        if self._is_stream_mode(state):
            events = append_done_event(events, result)

        # 写入语义缓存
        cache_question = state.get('cache_question') or self._question(state)
        self.runtime.store_semantic_cache(
            request,
            question=cache_question,
            cache_mode=self._cache_mode(state),
            response=result,
            answer_mode=state.get('raw_answer_mode') or 'unknown',
        )

        self._record_node('finalize', state, has_result=True)
        return {
            'result': result,
            'events': events,
        }

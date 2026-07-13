"""RAG 系统查询引擎主入口。

提供经典问答链路：单轮问答、多轮会话、流式输出、会话管理、多查询/HyDE/多改写、
上下文压缩、Corrective RAG 等全部能力。与主应用的 `app/rag/query_engine.py` 功能一致。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.chat_engine import CondenseQuestionChatEngine
from llama_index.core.memory import ChatMemoryBuffer

from app.rag_system.answer.preprocess import QueryPreprocessService
from app.rag_system.answer.semantic_cache import SemanticCacheService
from app.rag_system.answer.service import AnswerService
from app.rag_system.config.settings import RagSettings
from app.rag_system.knowledge.base import GroundedAnswerRequest
from app.rag_system.knowledge.service import RagKnowledgeCapability
from app.rag_system.models.query import ChatRequest, CitationItem, QueryRequest, QueryResponse
from app.rag_system.models.session import SessionMessage, SessionDetail, SessionSummaryItem, SessionSummaryResponse
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.retrieval.service import RagRetrievalService
from app.rag_system.store.persistence import RagPersistence
from app.rag_system.store.state import RagState
from app.rag_system.vector_store.llamaindex_adapter import build_llm, build_metadata_filters, build_vector_store


SSEEvent = dict[str, Any]


def _message_dict(role: str, content: str) -> dict[str, Any]:
    return {'role': role, 'content': content, 'timestamp': datetime.now(timezone.utc).isoformat()}


class RagQueryEngine:
    """经典查询引擎主类。

    提供单轮问答、多轮会话、流式输出和会话摘要等核心入口。
    """

    AUTO_SUMMARY_TRIGGER = 8
    SUMMARY_KEEP_RECENT = 4

    def __init__(
        self,
        settings: RagSettings,
        state: RagState,
        retrieval_service: RagRetrievalService,
        trace: TraceRecorder,
        persistence: RagPersistence | None = None,
        semantic_cache: SemanticCacheService | None = None,
        knowledge_capability: RagKnowledgeCapability | None = None,
    ) -> None:
        """初始化查询引擎。"""
        self.settings = settings
        self.state = state
        self.retrieval_service = retrieval_service
        self.trace = trace
        self.persistence = persistence
        self.semantic_cache = semantic_cache
        self.llm = build_llm(settings)
        self.preprocess_service = QueryPreprocessService(
            settings, retrieval_service, trace, self.llm,
        )
        self.answer_service = AnswerService(
            settings, trace, self.preprocess_service, self.llm,
        )
        self.knowledge_capability = knowledge_capability or RagKnowledgeCapability(
            state=state,
            retrieval=retrieval_service,
            vector_store=retrieval_service.vector_store,
            llm=self.llm,
        )

    # ── 公开入口：单轮问答 ─────────────────────────────────

    def query(self, payload: QueryRequest) -> QueryResponse:
        """执行经典单轮检索问答。"""
        question = payload.question.strip()
        guardrail_state = self._check_guardrails(question, payload, 'query')
        if guardrail_state['blocked']:
            return self._build_blocked_query_response(payload, guardrail_state)
        retrieval_question = self._prepare_retrieval_question(question, payload.use_query_rewrite, 'query')
        return self._run_query(
            payload,
            retrieval_question=retrieval_question,
            answer_question=question,
            guardrail_state=guardrail_state,
            cache_mode='query',
        )

    def stream_query(self, payload: QueryRequest) -> Iterator[SSEEvent]:
        """以经典 SSE 事件流方式输出单轮问答过程。"""
        question = payload.question.strip()
        guardrail_state = self._check_guardrails(question, payload, 'query_stream')
        rewrite_info = None
        retrieval_question = question
        if not guardrail_state['blocked']:
            retrieval_question, rewrite_info = self._resolve_rewrite_info(question, payload.use_query_rewrite, 'query_stream')
        yield from self._stream_query_events(
            payload=payload,
            retrieval_question=retrieval_question,
            answer_question=question,
            stream_mode='query_stream',
            cache_mode='query',
            rewrite_info=rewrite_info,
            guardrail_state=guardrail_state,
        )

    # ── 公开入口：多轮会话 ─────────────────────────────────

    def chat(self, payload: ChatRequest) -> QueryResponse:
        """执行经典多轮会话问答。"""
        question = payload.question.strip()
        guardrail_state = self._check_guardrails(question, payload, 'chat')
        if guardrail_state['blocked']:
            return self._build_blocked_chat_response(payload, guardrail_state, mode='guardrail_blocked')

        if self.llm is None:
            return self._chat_with_managed_retrieval(payload, mode='local_fallback', guardrail_state=guardrail_state)

        if payload.use_hyde or payload.use_long_context_reorder or payload.use_multi_query:
            return self._chat_with_managed_retrieval(payload, mode='managed', guardrail_state=guardrail_state)
        if payload.use_hybrid_retrieval:
            return self._chat_with_managed_retrieval(payload, mode='hybrid', guardrail_state=guardrail_state)
        if payload.use_graph_rag:
            return self._chat_with_managed_retrieval(payload, mode='graph_rag', guardrail_state=guardrail_state)
        if payload.use_parent_chunk_retrieval:
            return self._chat_with_managed_retrieval(payload, mode='parent_chunk', guardrail_state=guardrail_state)
        if payload.use_question_oriented_index:
            return self._chat_with_managed_retrieval(payload, mode='question_oriented', guardrail_state=guardrail_state)
        if payload.use_corrective_rag:
            return self._chat_with_managed_retrieval(payload, mode='corrective_rag', guardrail_state=guardrail_state)
        if self._use_context_compression(payload):
            return self._chat_with_managed_retrieval(payload, mode='context_compression', guardrail_state=guardrail_state)

        started = perf_counter()
        session = self._get_or_create_session(payload.session_id)
        history = self._to_llamaindex_history(session.get('messages', []), session.get('summary'))
        effective_filters = self._effective_filters(payload)
        stored_question = self._question_for_storage(question, guardrail_state)
        cache_question = self._question_for_storage(question, guardrail_state)

        cached_response, cache_info = self._lookup_semantic_cache(payload, cache_question, cache_mode='chat_engine')
        if cached_response is not None:
            session['messages'].append(_message_dict('user', stored_question))
            session['messages'].append(_message_dict('assistant', cached_response.answer))
            session['updated_at'] = datetime.now(timezone.utc)
            self._save_session(payload.session_id)
            self._auto_summarize_session(payload.session_id)
            latency_ms = int((perf_counter() - started) * 1000)
            self.trace.record('chat_completed', {
                'collection_name': payload.collection_name,
                'session_id': payload.session_id,
                'retrieved_count': len(cached_response.citations),
                'latency_ms': latency_ms,
                'chat_mode': 'llamaindex_chat_engine',
                'answer_mode': 'semantic_cache_hit',
                'semantic_cache': cache_info,
            })
            return cached_response.model_copy(update={'latency_ms': latency_ms})

        try:
            vs = build_vector_store(self.retrieval_service.vector_store, payload.collection_name)
            index = VectorStoreIndex.from_vector_store(
                vector_store=vs,
                embed_model=self.retrieval_service.embed_model,
            )
            qe = index.as_query_engine(
                llm=self.llm,
                similarity_top_k=payload.top_k,
                filters=build_metadata_filters(effective_filters),
            )
            memory = ChatMemoryBuffer.from_defaults(chat_history=history, llm=self.llm)
            chat_engine = CondenseQuestionChatEngine.from_defaults(query_engine=qe, memory=memory, llm=self.llm)
            chat_response = chat_engine.chat(guardrail_state.get('sanitized_question', question))
            citations = self._citations_from_source_nodes(chat_response.source_nodes, effective_filters)
            answer = str(chat_response).strip() or '未找到足够依据来回答该问题。'
        except Exception as exc:
            self.trace.record('llamaindex_chat_fallback', {'reason': str(exc), 'session_id': payload.session_id})
            return self._chat_with_managed_retrieval(payload, mode='llamaindex_fallback', guardrail_state=guardrail_state)

        session['messages'].append(_message_dict('user', stored_question))
        session['messages'].append(_message_dict('assistant', answer))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)
        self._auto_summarize_session(payload.session_id)

        latency_ms = int((perf_counter() - started) * 1000)
        response = QueryResponse(
            answer=answer, citations=citations,
            retrieved_count=len(citations), latency_ms=latency_ms, session_id=payload.session_id,
        )
        self._store_semantic_cache(
            payload, question=cache_question, cache_mode='chat_engine',
            response=response, answer_mode='llamaindex_chat_engine',
            metadata={'chat_mode': 'llamaindex_chat_engine'},
        )
        self.trace.record('chat_completed', {
            'collection_name': payload.collection_name, 'session_id': payload.session_id,
            'retrieved_count': len(citations), 'latency_ms': latency_ms,
            'chat_mode': 'llamaindex_chat_engine',
        })
        return response

    def stream_chat(self, payload: ChatRequest) -> Iterator[SSEEvent]:
        """以经典 SSE 事件流方式输出多轮会话过程。"""
        question = payload.question.strip()
        guardrail_state = self._check_guardrails(question, payload, 'chat_stream')
        session = self._get_or_create_session(payload.session_id)
        session['messages'].append(_message_dict('user', self._question_for_storage(question, guardrail_state)))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)

        rewrite_info = None
        retrieval_question = self._build_chat_retrieval_question(payload.session_id, question)
        if not guardrail_state['blocked']:
            retrieval_question, rewrite_info = self._resolve_rewrite_info(retrieval_question, payload.use_query_rewrite, 'chat_stream')

        final_response: QueryResponse | None = None
        try:
            for event in self._stream_query_events(
                payload=payload, retrieval_question=retrieval_question,
                answer_question=question, stream_mode='chat_stream', cache_mode='chat_stream',
                rewrite_info=rewrite_info, guardrail_state=guardrail_state,
            ):
                if event.get('event') == 'done':
                    final_payload = event.get('data', {}).get('response', {})
                    final_response = QueryResponse(**final_payload)
                yield event
        finally:
            if final_response is not None:
                session['messages'].append(_message_dict('assistant', final_response.answer))
                session['updated_at'] = datetime.now(timezone.utc)
                self._save_session(payload.session_id)
                self._auto_summarize_session(payload.session_id)

    # ── 会话管理 ───────────────────────────────────────────

    def get_session(self, session_id: str) -> SessionDetail | None:
        """返回指定会话的聚合详情。"""
        session = self.state.sessions.get(session_id)
        if session is None:
            return None
        messages = [SessionMessage(**item) for item in session.get('messages', [])]
        return SessionDetail(
            session_id=session_id,
            messages=messages,
            summary=session.get('summary'),
            updated_at=session.get('updated_at'),
        )

    def list_sessions(self) -> list[SessionSummaryItem]:
        """按更新时间倒序返回会话摘要列表。"""
        ordered = sorted(
            self.state.sessions.items(),
            key=lambda item: item[1].get('updated_at') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [
            SessionSummaryItem(
                session_id=sid,
                message_count=len(s.get('messages', [])),
                summary=s.get('summary'),
                updated_at=s.get('updated_at'),
            )
            for sid, s in ordered
        ]

    def summarize_session(self, session_id: str) -> SessionSummaryResponse | None:
        """主动触发指定会话的压缩摘要。"""
        session = self.state.sessions.get(session_id)
        if session is None:
            return None
        summary = self._compress_session(session_id)
        return SessionSummaryResponse(
            session_id=session_id, summary=summary,
            message_count=len(session.get('messages', [])),
            updated_at=session.get('updated_at'),
        )

    def check_guardrails(self, question: str, payload, trace_context: str) -> dict[str, Any]:
        """执行查询前的 guardrails 检查。"""
        return self._check_guardrails(question, payload, trace_context)

    def empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        """构造默认的脱敏状态对象。"""
        return self._empty_redaction_state(enabled)

    def sanitize_text(self, text: str, payload, *, target: str, trace_context: str) -> tuple[str, dict[str, Any]]:
        """按脱敏规则处理回答文本。"""
        return self._sanitize_text(text, payload, target=target, trace_context=trace_context)

    def sanitize_citations(self, citations: list[CitationItem], payload, trace_context: str) -> tuple[list[CitationItem], dict[str, Any]]:
        """对引用文本做脱敏处理。"""
        return self._sanitize_citations(citations, payload, trace_context)

    def question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        return self._question_for_storage(question, guardrail_state)

    def guardrail_block_message(self) -> str:
        return self._guardrail_block_message()

    def public_guardrail_state(self, guardrail_state, citation_redaction=None, answer_redaction=None) -> dict[str, Any]:
        return self._public_guardrail_state(guardrail_state, citation_redaction, answer_redaction)

    def lookup_semantic_cache(self, payload, question: str, cache_mode: str):
        return self._lookup_semantic_cache(payload, question, cache_mode)

    def store_semantic_cache(self, payload, *, question: str, cache_mode: str, response: QueryResponse, answer_mode: str, metadata=None):
        self._store_semantic_cache(payload, question=question, cache_mode=cache_mode, response=response, answer_mode=answer_mode, metadata=metadata)

    def graph_trace_flags(self, payload) -> dict[str, Any]:
        return self._graph_trace_flags(payload)

    def prepare_retrieval_question(self, question: str, use_query_rewrite: bool, trace_context: str) -> str:
        return self._prepare_retrieval_question(question, use_query_rewrite, trace_context)

    def resolve_rewrite_info(self, question: str, use_query_rewrite: bool, trace_context: str):
        return self._resolve_rewrite_info(question, use_query_rewrite, trace_context)

    def maybe_apply_multi_rewrite(self, payload, retrieval_question: str, answer_question: str, trace_context: str):
        return self._maybe_apply_multi_rewrite(payload, retrieval_question, answer_question, trace_context)

    def maybe_apply_multi_query(self, payload, retrieval_question: str, answer_question: str, trace_context: str):
        return self._maybe_apply_multi_query(payload, retrieval_question, answer_question, trace_context)

    def maybe_apply_hyde(self, payload, retrieval_question: str, answer_question: str, trace_context: str):
        return self._maybe_apply_hyde(payload, retrieval_question, answer_question, trace_context)

    def empty_corrective_info(self) -> dict[str, Any]:
        return self._empty_corrective_info()

    def prepare_answer_context(self, question: str, citations: list[CitationItem], payload):
        return self._prepare_answer_context(question, citations, payload)

    def use_context_compression(self, payload) -> bool:
        return self._use_context_compression(payload)

    def use_pii_redaction(self, payload) -> bool:
        return self._use_pii_redaction(payload)

    def build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        return self._build_chat_retrieval_question(session_id, current_question)

    # ── 内部实现：查询核心 ──

    def _run_query(
        self, payload, *, retrieval_question, answer_question, guardrail_state, cache_mode,
    ) -> QueryResponse:
        """核心查询编排：改写→多查询→HyDE→检索→压缩→答案。"""
        started = perf_counter()
        # 多查询 / HyDE / 多改写
        multi_queries, mq_info = self._maybe_apply_multi_query(payload, retrieval_question, answer_question, 'query')
        hyde_question, hyde_info = self._maybe_apply_hyde(payload, retrieval_question, answer_question, 'query')
        multi_rewrites, mr_info = self._maybe_apply_multi_rewrite(payload, retrieval_question, answer_question, 'query')
        is_multi = bool(multi_queries) or bool(multi_rewrites)

        # 语义缓存
        cache_question = self._question_for_storage(answer_question, guardrail_state)
        cached_response, cache_info = self._lookup_semantic_cache(payload, cache_question, cache_mode)
        if cached_response is not None:
            latency_ms = int((perf_counter() - started) * 1000)
            self.trace.record('query_completed', {
                'collection_name': payload.collection_name, 'answer_mode': 'semantic_cache_hit',
                'latency_ms': latency_ms, 'retrieved_count': len(cached_response.citations),
            })
            return cached_response.model_copy(update={'latency_ms': latency_ms})

        # 检索
        effective_question = hyde_question or retrieval_question
        if is_multi:
            all_questions = [effective_question]
            if multi_queries:
                all_questions.extend(multi_queries)
            if multi_rewrites:
                all_questions.extend(multi_rewrites)
            citations = self.retrieval_service.retrieve_multi(
                payload.collection_name, all_questions, effective_question, payload.top_k,
                filters=payload.filters, use_hybrid_retrieval=payload.use_hybrid_retrieval,
                use_rerank=payload.use_rerank, use_graph_rag=payload.use_graph_rag,
                graph_max_hops=payload.graph_max_hops,
            )
        else:
            citations = self.retrieval_service.retrieve(
                payload.collection_name, effective_question, payload.top_k,
                filters=payload.filters, use_hybrid_retrieval=payload.use_hybrid_retrieval,
                use_rerank=payload.use_rerank, use_graph_rag=payload.use_graph_rag,
                graph_max_hops=payload.graph_max_hops,
            )

        # 上下文压缩
        answer_context, compression_info = self._prepare_answer_context(answer_question, citations, payload)

        # 答案生成（含 Corrective RAG）
        use_guardrails = self._use_prompt_guardrails(payload)
        if payload.use_corrective_rag and self.llm:
            answer, corrective_info = self.answer_service.generate_corrective_answer(answer_question, answer_context)
            answer_mode = corrective_info.get('mode', 'corrective')
        else:
            prompt = self.answer_service.build_qa_prompt(answer_question, answer_context, use_guardrails=use_guardrails)
            answer, answer_mode = self.answer_service.generate_answer_with_mode(answer_question, prompt, citations, payload.collection_name)

        latency_ms = int((perf_counter() - started) * 1000)
        response = QueryResponse(
            answer=answer, citations=citations,
            retrieved_count=len(citations), latency_ms=latency_ms,
            answer_mode=answer_mode,
        )
        self._store_semantic_cache(payload, question=cache_question, cache_mode=cache_mode, response=response, answer_mode=answer_mode)
        self.trace.record('query_completed', {
            'collection_name': payload.collection_name, 'latency_ms': latency_ms,
            'retrieved_count': len(citations), 'answer_mode': answer_mode,
            'multi_query': mq_info, 'hyde': hyde_info, 'multi_rewrite': mr_info,
        })
        return response

    def _stream_query_events(
        self, *, payload, retrieval_question, answer_question, stream_mode, cache_mode, rewrite_info, guardrail_state,
    ) -> Iterator[SSEEvent]:
        """流式查询事件编排。"""
        started = perf_counter()
        multi_queries, mq_info = self._maybe_apply_multi_query(payload, retrieval_question, answer_question, stream_mode)
        hyde_question, hyde_info = self._maybe_apply_hyde(payload, retrieval_question, answer_question, stream_mode)
        multi_rewrites, mr_info = self._maybe_apply_multi_rewrite(payload, retrieval_question, answer_question, stream_mode)
        is_multi = bool(multi_queries) or bool(multi_rewrites)

        yield {'event': 'search_start', 'data': {'question': retrieval_question}}

        effective_question = hyde_question or retrieval_question
        if is_multi:
            all_qs = [effective_question]
            if multi_queries: all_qs.extend(multi_queries)
            if multi_rewrites: all_qs.extend(multi_rewrites)
            citations = self.retrieval_service.retrieve_multi(
                payload.collection_name, all_qs, effective_question, payload.top_k,
                filters=payload.filters, use_hybrid_retrieval=payload.use_hybrid_retrieval,
                use_rerank=payload.use_rerank, use_graph_rag=payload.use_graph_rag,
                graph_max_hops=payload.graph_max_hops,
            )
        else:
            citations = self.retrieval_service.retrieve(
                payload.collection_name, effective_question, payload.top_k,
                filters=payload.filters, use_hybrid_retrieval=payload.use_hybrid_retrieval,
                use_rerank=payload.use_rerank, use_graph_rag=payload.use_graph_rag,
                graph_max_hops=payload.graph_max_hops,
            )

        yield {'event': 'search_done', 'data': {'count': len(citations)}}

        answer_context, compression_info = self._prepare_answer_context(answer_question, citations, payload)
        full_answer = ''
        use_guardrails = self._use_prompt_guardrails(payload)
        prompt = self.answer_service.build_qa_prompt(answer_question, answer_context, use_guardrails=use_guardrails)

        for chunk in self.answer_service.generate_answer_stream(prompt):
            full_answer += chunk
            yield {'event': 'delta', 'data': chunk}

        latency_ms = int((perf_counter() - started) * 1000)
        response = QueryResponse(
            answer=full_answer, citations=citations,
            retrieved_count=len(citations), latency_ms=latency_ms,
        )
        self._store_semantic_cache(payload, question=self._question_for_storage(answer_question, guardrail_state), cache_mode=cache_mode, response=response, answer_mode='stream')
        yield {'event': 'done', 'data': {'response': response.model_dump()}}

    def _chat_with_managed_retrieval(self, payload, *, mode: str, guardrail_state: dict) -> QueryResponse:
        """受控链路的多轮会话（不走 llamaindex chat engine）。"""
        started = perf_counter()
        question = payload.question.strip()
        retrieval_question = self._prepare_retrieval_question(question, payload.use_query_rewrite, 'chat')
        citations = self.retrieval_service.retrieve(
            payload.collection_name, retrieval_question, payload.top_k,
            filters=payload.filters, use_hybrid_retrieval=payload.use_hybrid_retrieval,
            use_rerank=payload.use_rerank, use_graph_rag=payload.use_graph_rag,
            graph_max_hops=payload.graph_max_hops,
        )
        contexts = [c.text for c in citations]
        if payload.use_corrective_rag and self.llm:
            answer, info = self.answer_service.generate_corrective_answer(question, contexts)
            answer_mode = info.get('mode', 'corrective')
        else:
            prompt = self.answer_service.build_qa_prompt(question, contexts)
            answer, answer_mode = self.answer_service.generate_answer_with_mode(question, prompt, citations, payload.collection_name)

        session = self._get_or_create_session(payload.session_id)
        session['messages'].append(_message_dict('user', question))
        session['messages'].append(_message_dict('assistant', answer))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)

        latency_ms = int((perf_counter() - started) * 1000)
        return QueryResponse(
            answer=answer, citations=citations,
            retrieved_count=len(citations), latency_ms=latency_ms,
            session_id=payload.session_id, answer_mode=mode,
        )

    # ── Guardrails / 脱敏 ──────────────────────────────────

    def _check_guardrails(self, question: str, payload, trace_context: str) -> dict[str, Any]:
        if self._use_prompt_guardrails(payload):
            result = self.preprocess_service.check_guardrails(question)
            sanitized, redaction = self.preprocess_service.redact_pii(question)
            result['sanitized_question'] = sanitized
            result['redaction'] = redaction
            return result
        return {'blocked': False, 'risk': 'none', 'sanitized_question': question, 'redaction': {}}

    def _use_prompt_guardrails(self, payload) -> bool:
        if hasattr(payload, 'use_prompt_guardrails') and payload.use_prompt_guardrails is not None:
            return payload.use_prompt_guardrails
        return self.settings.enable_prompt_guardrails

    def _use_pii_redaction(self, payload) -> bool:
        if hasattr(payload, 'use_pii_redaction') and payload.use_pii_redaction is not None:
            return payload.use_pii_redaction
        return self.settings.enable_pii_redaction

    def _use_context_compression(self, payload) -> bool:
        if hasattr(payload, 'use_context_compression') and payload.use_context_compression is not None:
            return payload.use_context_compression
        return self.settings.enable_context_compression

    def _sanitize_text(self, text: str, payload, *, target: str, trace_context: str) -> tuple[str, dict[str, Any]]:
        if self._use_pii_redaction(payload):
            return self.preprocess_service.redact_pii(text)
        return text, {'none': True}

    def _sanitize_citations(self, citations: list[CitationItem], payload, trace_context: str) -> tuple[list[CitationItem], dict[str, Any]]:
        if not self._use_pii_redaction(payload):
            return citations, {'none': True}
        redaction: dict[str, Any] = {'applied': False}
        for c in citations:
            c.text, _ = self.preprocess_service.redact_pii(c.text)
        redaction['applied'] = True
        return citations, redaction

    def _question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        return guardrail_state.get('sanitized_question', question)

    def _guardrail_block_message(self) -> str:
        return '请求被安全策略拦截，拒绝执行。'

    def _public_guardrail_state(self, guardrail_state, citation_redaction=None, answer_redaction=None) -> dict[str, Any]:
        return {'blocked': guardrail_state.get('blocked', False), 'risk': guardrail_state.get('risk', 'none')}

    def _empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        return {'enabled': enabled, 'applied': False}

    # ── 查询改写 / 多查询 / HyDE ───────────────────────────

    def _prepare_retrieval_question(self, question: str, use_rewrite: bool, trace_context: str) -> str:
        if not use_rewrite:
            return question
        return self.preprocess_service.rewrite_query(question, '')

    def _resolve_rewrite_info(self, question: str, use_query_rewrite: bool, trace_context: str):
        if not use_query_rewrite:
            return question, None
        rewritten = self._prepare_retrieval_question(question, True, trace_context)
        info = {'original': question, 'rewritten': rewritten} if rewritten != question else None
        return rewritten, info

    def _maybe_apply_multi_query(self, payload, retrieval_question: str, answer_question: str, trace_context: str):
        if not getattr(payload, 'use_multi_query', False) or not self.llm:
            return [], None
        queries = self.preprocess_service.expand_multi_query(answer_question, getattr(payload, 'multi_query_count', 3))
        info = {'count': len(queries), 'queries': queries}
        self.trace.record('multi_query', info)
        return queries, info

    def _maybe_apply_multi_rewrite(self, payload, retrieval_question: str, answer_question: str, trace_context: str):
        if not getattr(payload, 'use_multi_rewrite', False) or not self.llm:
            return [], None
        count = getattr(payload, 'multi_rewrite_count', 3)
        rewrites = [f'{answer_question} 第{i+1}种表述' for i in range(count)]
        info = {'count': len(rewrites), 'rewrites': rewrites}
        self.trace.record('multi_rewrite', info)
        return rewrites, info

    def _maybe_apply_hyde(self, payload, retrieval_question: str, answer_question: str, trace_context: str):
        if not getattr(payload, 'use_hyde', False) or not self.llm:
            return retrieval_question, None
        hyde_doc = self.preprocess_service.generate_hyde(answer_question)
        info = {'hyde_length': len(hyde_doc)}
        self.trace.record('hyde_generated', info)
        return hyde_doc, info

    def _empty_corrective_info(self) -> dict[str, Any]:
        return {'enabled': False}

    def _graph_trace_flags(self, payload) -> dict[str, Any]:
        return {
            'use_graph_rag': getattr(payload, 'use_graph_rag', False),
            'graph_max_hops': getattr(payload, 'graph_max_hops', 1),
        }

    # ── 上下文压缩 ──

    def _prepare_answer_context(self, question: str, citations: list[CitationItem], payload):
        contexts = [c.text for c in citations]
        if self._use_context_compression(payload) and len(contexts) > 1:
            return self.answer_service.compress_citation_contexts(question, contexts, citations)
        return contexts, {'compressed': False}

    # ── 语义缓存 ──

    def _lookup_semantic_cache(self, payload, question: str, cache_mode: str):
        if not self.semantic_cache:
            return None, {'cache_enabled': False}
        strategy_sig = self._build_strategy_signature(payload)
        cached, info = self.semantic_cache.lookup(
            collection_name=payload.collection_name, mode=cache_mode,
            question=question, filters=getattr(payload, 'filters', None),
            strategy_signature=strategy_sig,
        )
        if cached:
            return QueryResponse(
                answer=cached.get('answer', ''),
                citations=[CitationItem(**c) for c in cached.get('citations', [])],
                retrieved_count=len(cached.get('citations', [])),
            ), info
        return None, info

    def _store_semantic_cache(self, payload, *, question: str, cache_mode: str, response: QueryResponse, answer_mode: str, metadata=None):
        if not self.semantic_cache:
            return
        self.semantic_cache.store(
            collection_name=payload.collection_name, mode=cache_mode,
            question=question, answer=response.answer,
            citations=[c.model_dump() for c in response.citations],
            strategy_signature=self._build_strategy_signature(payload),
            metadata=metadata,
        )

    def _build_strategy_signature(self, payload) -> str:
        parts = [
            str(getattr(payload, 'use_hybrid_retrieval', False)),
            str(getattr(payload, 'use_rerank', True)),
            str(getattr(payload, 'use_graph_rag', False)),
            str(getattr(payload, 'use_corrective_rag', False)),
            str(getattr(payload, 'top_k', 5)),
        ]
        return ':'.join(parts)

    # ── 会话管理 ──

    def _get_or_create_session(self, session_id: str) -> dict[str, Any]:
        if session_id and session_id in self.state.sessions:
            return self.state.sessions[session_id]
        if session_id and self.persistence:
            existing = self.persistence.get_session(session_id)
            if existing:
                self.state.sessions[session_id] = existing
                return existing
        session = {
            'session_id': session_id, 'messages': [],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        if session_id:
            self.state.sessions[session_id] = session
        return session

    def _save_session(self, session_id: str) -> None:
        if not session_id:
            return
        session = self.state.sessions.get(session_id)
        if session and self.persistence:
            self.persistence.upsert_session(session_id, session)

    def _auto_summarize_session(self, session_id: str) -> None:
        session = self.state.sessions.get(session_id)
        if session is None:
            return
        messages = session.get('messages', [])
        if len(messages) < self.AUTO_SUMMARY_TRIGGER:
            return
        # 仅当摘要尚未生成或消息数再次达到阈值时才触发
        if session.get('summary') and len(messages) < self.AUTO_SUMMARY_TRIGGER * 2:
            return
        self._compress_session(session_id)

    def _compress_session(self, session_id: str) -> str:
        """用 LLM 压缩历史会话。"""
        session = self.state.sessions.get(session_id)
        if session is None:
            return ''
        messages = session.get('messages', [])
        if not messages or not self.llm:
            return session.get('summary', '')

        text = '\n'.join(f"{m.get('role','')}: {m.get('content','')[:200]}" for m in messages[-10:])
        try:
            response = self.llm.complete(f"请摘要以下对话：\n{text}")
            summary = str(response).strip()[:500]
            session['summary'] = summary
            if self.persistence:
                self.persistence.upsert_session(session_id, session)
            return summary
        except Exception:
            return session.get('summary', '')

    def _build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        """结合会话历史生成聊天模式下的检索问题。"""
        session = self.state.sessions.get(session_id)
        if not session:
            return current_question
        history = session.get('messages', [])
        if not history:
            return current_question
        recent = history[-3:]
        context = ' | '.join(f"{m.get('role','')}: {m.get('content','')[:100]}" for m in recent)
        return f"{context} | {current_question}"

    # ── 过滤 ──

    def _effective_filters(self, payload) -> dict[str, Any] | None:
        filters = {}
        if hasattr(payload, 'filters') and payload.filters:
            filters.update(payload.filters)
        if hasattr(payload, 'permission_scope') and payload.permission_scope:
            filters['permission'] = payload.permission_scope
        if hasattr(payload, 'allowed_permissions') and payload.allowed_permissions:
            filters['permission'] = {'$in': payload.allowed_permissions}
        return filters or None

    # ── LlamaIndex 辅助 ──

    def _to_llamaindex_history(self, messages: list[dict[str, Any]], summary: str | None = None):
        """将原生消息列表转换为 llama-index 可消费的 ChatMessage 列表。"""
        from llama_index.core.base.llms.types import ChatMessage, MessageRole
        history: list[ChatMessage] = []
        if summary:
            history.append(ChatMessage(role=MessageRole.SYSTEM, content=f'历史会话摘要：{summary}'))
        for msg in messages:
            role = MessageRole.USER if msg.get('role') == 'user' else MessageRole.ASSISTANT
            history.append(ChatMessage(role=role, content=msg.get('content', '')))
        return history

    def _citations_from_source_nodes(self, source_nodes, effective_filters) -> list[CitationItem]:
        citations: list[CitationItem] = []
        for node in source_nodes:
            meta = node.metadata if hasattr(node, 'metadata') else {}
            citations.append(CitationItem(
                chunk_id=node.node.node_id if hasattr(node, 'node') and hasattr(node.node, 'node_id') else str(id(node)),
                source=meta.get('file_name', ''),
                file_path=meta.get('file_path', ''),
                page=meta.get('page'),
                score=float(node.score) if node.score is not None else None,
                text=str(node.text) if hasattr(node, 'text') else '',
                section_title=meta.get('section_title'),
            ))
        return citations

    # ── 公开入口兼容：单轮问答（无增强） ──

    def _build_blocked_query_response(self, payload: QueryRequest, guardrail_state: dict) -> QueryResponse:
        return QueryResponse(
            answer=self._guardrail_block_message(),
            citations=[], retrieved_count=0,
            answer_mode='guardrail_blocked',
        )

    def _build_blocked_chat_response(self, payload: ChatRequest, guardrail_state: dict, mode: str = 'guardrail_blocked') -> QueryResponse:
        return QueryResponse(
            answer=self._guardrail_block_message(),
            citations=[], retrieved_count=0,
            answer_mode=mode,
        )

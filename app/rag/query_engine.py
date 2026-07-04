"""RAG 查询引擎主入口。

该文件只保留面对调用方的同步入口和核心编排流程，流式输出、权限与缓存策略、
会话压缩以及若干回答增强能力已经拆到 `query_engine_parts` 子模块中，
方便在不改变 `RagQueryEngine` 对外接口的前提下继续演进内部实现。
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Iterator
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.chat_engine import CondenseQuestionChatEngine
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.memory import ChatMemoryBuffer

from app.capabilities.knowledge import (
    GroundedAnswerRequest,
    GroundedAnswerStrategy,
    KnowledgeCapability,
    build_knowledge_capability,
)
from app.core.config import Settings
from app.harness.model_router import ModelRouter
from app.models.query import ChatRequest, CitationItem, QueryRequest, QueryResponse
from app.models.session import SessionDetail, SessionMessage, SessionSummaryItem, SessionSummaryResponse
from app.rag.llamaindex_components import build_llm, build_metadata_filters, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.retrieval import RagRetrievalService
from app.services.system_settings import RuntimeConfigReader
from app.services.answer_service import AnswerService
from app.services.query_preprocess_service import QueryPreprocessService
from app.services.semantic_cache import SemanticCacheService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import MetadataFilters as MetadataFiltersMap, SSEEvent, SessionMessageRecord, SessionRecord

from app.rag.query_engine_parts.streaming import QueryEngineStreamingMixin
from app.rag.query_engine_parts.policy_cache import QueryEnginePolicyCacheMixin
from app.rag.query_engine_parts.answer_session import QueryEngineAnswerSessionMixin


class RagQueryEngine(QueryEngineStreamingMixin, QueryEnginePolicyCacheMixin, QueryEngineAnswerSessionMixin):
    """经典查询引擎主类。

    这里保留经典问答链路最核心的入口：单轮问答、多轮会话、流式输出和会话摘要。
    """

    AUTO_SUMMARY_TRIGGER = 8
    SUMMARY_KEEP_RECENT = 4

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        retrieval_service: RagRetrievalService,
        trace: TraceRecorder,
        persistence: SQLiteStateStore | None = None,
        semantic_cache: SemanticCacheService | None = None,
        knowledge_capability: KnowledgeCapability | None = None,
        runtime_config: RuntimeConfigReader | None = None,
    ) -> None:
        """初始化查询引擎和可选的 LLM 运行时。"""
        self.settings = settings
        self.state = state
        self.retrieval_service = retrieval_service
        self.trace = trace
        self.persistence = persistence
        self.semantic_cache = semantic_cache
        self.llm = build_llm(settings)
        self.model_router = ModelRouter()
        self._runtime_config = runtime_config
        self.preprocess_service = QueryPreprocessService(
            settings, retrieval_service, trace, self.llm,
            runtime_config=runtime_config,
        )
        self.answer_service = AnswerService(
            settings, trace, self.preprocess_service, self.llm,
            runtime_config=runtime_config,
        )
        self.knowledge_capability = knowledge_capability or build_knowledge_capability(
            settings=settings,
            state=state,
            retrieval=retrieval_service,
            vector_store=getattr(retrieval_service, 'vector_store', None),
            llm=self.llm,
            model_router=self.model_router,
        )

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
            stream_mode='query',
            cache_mode='query',
            rewrite_info=rewrite_info,
            guardrail_state=guardrail_state,
        )

    def chat(self, payload: ChatRequest) -> QueryResponse:
        """执行经典多轮会话问答。"""
        question = payload.question.strip()
        guardrail_state = self._check_guardrails(question, payload, 'chat')
        if guardrail_state['blocked']:
            return self._build_blocked_chat_response(payload, guardrail_state, mode='guardrail_blocked')

        if self.llm is None:
            # 未配置远程 LLM 时，退化为受控检索 + 本地答案拼装模式。
            return self._chat_with_managed_retrieval(payload, mode='local_fallback', guardrail_state=guardrail_state)

        # 这些增强能力依赖受控编排链路，不能直接走 llamaindex chat engine。
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
        # 聊天模式缓存命中后仍然写入 session，这样前端看到的会话轨迹与真实回答一致。
        cached_response, cache_info = self._lookup_semantic_cache(payload, cache_question, cache_mode='chat_engine')
        if cached_response is not None:
            session['messages'].append(self._message('user', stored_question))
            session['messages'].append(self._message('assistant', cached_response.answer))
            session['updated_at'] = datetime.now(timezone.utc)
            self._save_session(payload.session_id)
            self._auto_summarize_session(payload.session_id)
            latency_ms = int((perf_counter() - started) * 1000)
            self.trace.record(
                'chat_completed',
                {
                    'collection_name': payload.collection_name,
                    'session_id': payload.session_id,
                    'retrieved_count': len(cached_response.citations),
                    'latency_ms': latency_ms,
                    'chat_mode': 'llamaindex_chat_engine',
                    'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                    **self._graph_trace_flags(payload),
                    'answer_mode': 'semantic_cache_hit',
                    'semantic_cache': cache_info,
                    'guardrails': self._public_guardrail_state(guardrail_state),
                },
            )
            return cached_response.model_copy(update={'latency_ms': latency_ms})

        try:
            vector_store = build_vector_store(self.retrieval_service.vector_store, payload.collection_name)
            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.retrieval_service.embed_model,
            )
            query_engine = index.as_query_engine(
                llm=self.llm,
                similarity_top_k=payload.top_k,
                filters=build_metadata_filters(effective_filters),
            )
            memory = ChatMemoryBuffer.from_defaults(chat_history=history, llm=self.llm)
            chat_engine = CondenseQuestionChatEngine.from_defaults(
                query_engine=query_engine,
                memory=memory,
                llm=self.llm,
            )
            chat_response = chat_engine.chat(guardrail_state['sanitized_question'])
            citations = self._citations_from_source_nodes(chat_response.source_nodes, effective_filters)
            citations, citation_redaction = self._sanitize_citations(citations, payload, 'chat')
            answer = str(chat_response).strip() or '未找到足够依据来回答该问题。'
            answer, answer_redaction = self._sanitize_text(answer, payload, target='answer', trace_context='chat')
        except Exception as exc:
            # llamaindex chat engine 失败时回退到受控编排链路，避免远端运行时问题直接中断会话。
            self.trace.record('llamaindex_chat_fallback', {'reason': str(exc), 'session_id': payload.session_id})
            return self._chat_with_managed_retrieval(payload, mode='llamaindex_fallback', guardrail_state=guardrail_state)

        session['messages'].append(self._message('user', stored_question))
        session['messages'].append(self._message('assistant', answer))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)
        self._auto_summarize_session(payload.session_id)

        latency_ms = int((perf_counter() - started) * 1000)
        response = QueryResponse(
            answer=answer,
            citations=citations,
            retrieved_count=len(citations),
            latency_ms=latency_ms,
            session_id=payload.session_id,
        )
        self._store_semantic_cache(
            payload,
            question=cache_question,
            cache_mode='chat_engine',
            response=response,
            answer_mode='llamaindex_chat_engine',
            metadata={'chat_mode': 'llamaindex_chat_engine'},
        )
        self.trace.record(
            'chat_completed',
            {
                'collection_name': payload.collection_name,
                'session_id': payload.session_id,
                'retrieved_count': len(citations),
                'latency_ms': latency_ms,
                'chat_mode': 'llamaindex_chat_engine',
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                **self._graph_trace_flags(payload),
                'semantic_cache': cache_info,
                'guardrails': self._public_guardrail_state(guardrail_state, citation_redaction, answer_redaction),
            },
        )
        return response

    def stream_chat(self, payload: ChatRequest) -> Iterator[SSEEvent]:
        """以经典 SSE 事件流方式输出多轮会话过程。"""
        question = payload.question.strip()
        guardrail_state = self._check_guardrails(question, payload, 'chat_stream')
        session = self._get_or_create_session(payload.session_id)
        session['messages'].append(self._message('user', self._question_for_storage(question, guardrail_state)))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)

        rewrite_info = None
        retrieval_question = self._build_chat_question(payload.session_id)
        if not guardrail_state['blocked']:
            retrieval_question, rewrite_info = self._resolve_rewrite_info(
                retrieval_question,
                payload.use_query_rewrite,
                'chat_stream',
            )

        final_response: QueryResponse | None = None
        try:
            for event in self._stream_query_events(
                payload=payload,
                retrieval_question=retrieval_question,
                answer_question=question,
                stream_mode='chat_stream',
                cache_mode='chat_stream',
                rewrite_info=rewrite_info,
                guardrail_state=guardrail_state,
            ):
                if event['event'] == 'done':
                    final_payload = event.get('data', {}).get('response', {})
                    final_response = QueryResponse(**final_payload)
                yield event
        finally:
            if final_response is not None:
                session['messages'].append(self._message('assistant', final_response.answer))
                session['updated_at'] = datetime.now(timezone.utc)
                self._save_session(payload.session_id)
                self._auto_summarize_session(payload.session_id)

    def get_session(self, session_id: str) -> SessionDetail | None:
        """返回指定会话的聚合详情。"""
        session = self.state.sessions.get(session_id)
        if session is None:
            return None

        messages = [SessionMessage(**item) for item in session.get('messages', [])]
        return SessionDetail(
            session_id=session_id,
            message_count=len(messages),
            summary=session.get('summary'),
            summary_updated_at=session.get('summary_updated_at'),
            compressed_message_count=session.get('compressed_message_count', 0),
            updated_at=session.get('updated_at'),
            messages=messages,
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
                session_id=session_id,
                message_count=len(session.get('messages', [])),
                summary=session.get('summary'),
                summary_updated_at=session.get('summary_updated_at'),
                compressed_message_count=session.get('compressed_message_count', 0),
                updated_at=session.get('updated_at'),
            )
            for session_id, session in ordered
        ]

    def summarize_session(self, session_id: str) -> SessionSummaryResponse | None:
        """主动触发指定会话的压缩摘要。"""
        session = self.state.sessions.get(session_id)
        if session is None:
            return None

        summary = self._compress_session(session_id)
        updated_at = session.get('summary_updated_at') or datetime.now(timezone.utc)
        return SessionSummaryResponse(
            session_id=session_id,
            summary=summary,
            compressed_message_count=session.get('compressed_message_count', 0),
            updated_at=updated_at,
        )

    # Query workflow/runtime stable surface

    def check_guardrails(self, question: str, payload: QueryRequest, trace_context: str) -> dict[str, Any]:
        """执行查询前的轻量 guardrails 检查并返回结构化状态。"""
        return self._check_guardrails(question, payload, trace_context)

    def empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        """构造默认的脱敏状态对象。"""
        return self._empty_redaction_state(enabled)

    def sanitize_text(
        self,
        text: str,
        payload: QueryRequest,
        *,
        target: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any]]:
        """按脱敏规则处理回答文本，必要时记录命中的敏感模式。"""
        return self._sanitize_text(text, payload, target=target, trace_context=trace_context)

    def sanitize_citations(
        self,
        citations: list[CitationItem],
        payload: QueryRequest,
        trace_context: str,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """对引用文本做同样的脱敏处理，保持回答与引用一致。"""
        return self._sanitize_citations(citations, payload, trace_context)

    def question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        """基于脱敏后的问题文本构造缓存与会话可持久化内容。"""
        return self._question_for_storage(question, guardrail_state)

    def guardrail_block_message(self) -> str:
        """返回命中 guardrails 时的统一拦截提示语。"""
        return self._guardrail_block_message()

    def public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """将内部 guardrail 状态裁剪成可安全暴露的公共结构。"""
        return self._public_guardrail_state(guardrail_state, citation_redaction, answer_redaction)

    def lookup_semantic_cache(
        self,
        payload: QueryRequest,
        question: str,
        cache_mode: str,
    ) -> tuple[QueryResponse | None, dict[str, Any]]:
        """查询语义缓存，并返回命中结果及其事件信息。"""
        return self._lookup_semantic_cache(payload, question, cache_mode)

    def store_semantic_cache(
        self,
        payload: QueryRequest,
        *,
        question: str,
        cache_mode: str,
        response: QueryResponse,
        answer_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把本次回答结果写入语义缓存。"""
        self._store_semantic_cache(
            payload,
            question=question,
            cache_mode=cache_mode,
            response=response,
            answer_mode=answer_mode,
            metadata=metadata,
        )

    def graph_trace_flags(self, payload: QueryRequest) -> dict[str, Any]:
        """整理 GraphRAG 相关开关，供 trace 与事件复用。"""
        return self._graph_trace_flags(payload)

    def prepare_retrieval_question(self, question: str, use_query_rewrite: bool, trace_context: str) -> str:
        """按改写配置生成用于检索阶段的问题文本。"""
        return self._prepare_retrieval_question(question, use_query_rewrite, trace_context)

    def resolve_rewrite_info(
        self,
        question: str,
        use_query_rewrite: bool,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """生成查询改写后的展示信息与事件载荷。"""
        return self._resolve_rewrite_info(question, use_query_rewrite, trace_context)

    def maybe_apply_multi_rewrite(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """根据配置决定是否生成多重改写候选。"""
        return self._maybe_apply_multi_rewrite(payload, retrieval_question, answer_question, trace_context)

    def maybe_apply_multi_query(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """根据配置决定是否启用多查询检索策略。"""
        return self._maybe_apply_multi_query(payload, retrieval_question, answer_question, trace_context)

    def maybe_apply_hyde(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """根据配置决定是否生成 HyDE 检索问题。"""
        return self._maybe_apply_hyde(payload, retrieval_question, answer_question, trace_context)

    def empty_corrective_info(self) -> dict[str, Any]:
        """返回未启用纠错式 RAG 时的默认状态结构。"""
        return self._empty_corrective_info()

    def prepare_answer_context(
        self,
        question: str,
        citations: list[CitationItem],
        payload: QueryRequest,
    ) -> tuple[list[str], dict[str, Any]]:
        """整理回答阶段使用的上下文与压缩信息。"""
        return self._prepare_answer_context(question, citations, payload)

    def use_context_compression(self, payload: QueryRequest) -> bool:
        """判断当前请求是否启用上下文压缩。"""
        return self._use_context_compression(payload)

    def use_pii_redaction(self, payload: QueryRequest) -> bool:
        """判断当前请求是否启用 PII 脱敏。"""
        return self._use_pii_redaction(payload)

    def build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        """结合会话历史生成聊天模式下的检索问题。"""
        return self._build_chat_retrieval_question(session_id, current_question)

    def retrieve_citations(
        self,
        payload: QueryRequest,
        retrieval_questions: list[str],
        answer_question: str,
    ) -> list[CitationItem]:
        """执行检索并返回标准化后的引用列表。"""
        return self._retrieve_citations(payload, retrieval_questions, answer_question)

    def stream_citation_snapshot(self, citations: list[CitationItem], limit: int = 3) -> list[dict[str, Any]]:
        """提取适合在流式阶段提前展示的引用快照。"""
        return self._stream_citation_snapshot(citations, limit=limit)

    def generate_answer_with_mode(
        self,
        *,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str]:
        """按回答模式生成最终文本，并返回模式标记。"""
        return self._generate_answer_with_mode(question, prompt, citations, collection_name)

    def maybe_apply_corrective_rag(
        self,
        *,
        payload: QueryRequest,
        question: str,
        answer: str,
        answer_mode: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """根据配置与上下文决定是否执行纠错式 RAG。"""
        return self._maybe_apply_corrective_rag(
            payload=payload,
            question=question,
            answer=answer,
            answer_mode=answer_mode,
            citations=citations,
            collection_name=collection_name,
        )

    def message(self, role: str, content: str) -> SessionMessageRecord:
        """构造可写入 session 的标准消息记录。"""
        return self._message(role, content)

    def get_or_create_session(self, session_id: str) -> SessionRecord:
        """读取现有会话，缺失时创建新的会话记录。"""
        return self._get_or_create_session(session_id)

    def save_session(self, session_id: str) -> None:
        """把当前会话状态持久化到后端存储。"""
        self._save_session(session_id)

    def auto_summarize_session(self, session_id: str) -> None:
        """在消息积累到一定规模后自动刷新会话摘要。"""
        self._auto_summarize_session(session_id)

    def chunk_text_for_stream(self, text: str, chunk_size: int = 24) -> list[str]:
        """把长文本按固定块大小切分为流式输出片段。"""
        return self._chunk_text_for_stream(text, chunk_size=chunk_size)

    def _run_query(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        guardrail_state: dict[str, Any] | None = None,
        cache_mode: str = 'query',
    ) -> QueryResponse:
        """执行检索、Prompt 构造和答案生成的标准流程。"""
        started = perf_counter()
        guardrail_state = guardrail_state or self._check_guardrails(answer_question, payload, 'query')
        if guardrail_state['blocked']:
            return self._build_blocked_query_response(payload, guardrail_state, started=started)
        cache_question = self._question_for_storage(answer_question, guardrail_state)
        retrieval_questions, multi_rewrite_info = self._maybe_apply_multi_rewrite(
            payload=payload,
            retrieval_question=retrieval_question,
            answer_question=answer_question,
            trace_context='query',
        )
        multi_query_questions, multi_query_info = self._maybe_apply_multi_query(
            payload=payload,
            retrieval_question=retrieval_questions[0],
            answer_question=answer_question,
            trace_context='query',
        )
        if payload.use_multi_query and multi_query_info is not None and multi_query_info.get('enabled'):
            retrieval_questions = multi_query_questions
        retrieval_question, hyde_info = self._maybe_apply_hyde(
            payload=payload,
            retrieval_question=retrieval_questions[0],
            answer_question=answer_question,
            trace_context='query',
        )
        if retrieval_questions:
            retrieval_questions = [retrieval_question, *retrieval_questions[1:]]
        else:
            retrieval_questions = [retrieval_question]
        if payload.use_hyde and hyde_info is not None and hyde_info.get('enabled'):
            retrieval_questions = [retrieval_question]
        cached_response, cache_info = self._lookup_semantic_cache(payload, cache_question, cache_mode=cache_mode)
        if cached_response is not None:
            contexts, compression_info = self._prepare_answer_context(answer_question, cached_response.citations, payload)
            latency_ms = int((perf_counter() - started) * 1000)
            self.trace.record(
                'query_completed',
                {
                    'collection_name': payload.collection_name,
                    'retrieved_count': len(cached_response.citations),
                    'latency_ms': latency_ms,
                    'retrieval_question': retrieval_questions[0][:200],
                    'retrieval_questions': [item[:200] for item in retrieval_questions[:6]],
                    'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                    'use_multi_query': payload.use_multi_query,
                    'multi_query': multi_query_info,
                    'use_multi_rewrite': payload.use_multi_rewrite,
                    'multi_rewrite': multi_rewrite_info,
                    'use_hyde': payload.use_hyde,
                    'hyde': hyde_info,
                    'use_long_context_reorder': payload.use_long_context_reorder,
                    'use_parent_chunk_retrieval': payload.use_parent_chunk_retrieval,
                    'use_question_oriented_index': payload.use_question_oriented_index,
                    'use_corrective_rag': payload.use_corrective_rag,
                    **self._graph_trace_flags(payload),
                    'answer_mode': 'semantic_cache_hit',
                    'corrective_rag': self._empty_corrective_info(),
                    'use_context_compression': compression_info['enabled'],
                    'context_compression': compression_info,
                    'semantic_cache': cache_info,
                    'guardrails': self._public_guardrail_state(guardrail_state),
                },
            )
            return cached_response.model_copy(update={'latency_ms': latency_ms})
        grounded_result = self._grounded_answer_via_knowledge_capability(
            payload=payload,
            retrieval_questions=retrieval_questions,
            answer_question=guardrail_state['sanitized_question'],
            trace_context='query',
        )
        if grounded_result is not None:
            citations = grounded_result['citations']
            answer = grounded_result['answer']
            answer_mode = grounded_result['answer_mode']
            corrective_info = grounded_result['corrective_info']
            compression_info = grounded_result['compression_info']
            citation_redaction = grounded_result['citation_redaction']
            answer_redaction = grounded_result['answer_redaction']
            latency_ms = int((perf_counter() - started) * 1000)
            response = QueryResponse(
                answer=answer,
                citations=citations,
                retrieved_count=len(citations),
                latency_ms=latency_ms,
                session_id=payload.session_id,
            )
            self._store_semantic_cache(
                payload,
                question=cache_question,
                cache_mode=cache_mode,
                response=response,
                answer_mode=answer_mode,
                metadata={
                    'retrieval_questions': retrieval_questions[:6],
                    'corrective_rag': corrective_info,
                    'context_compression': compression_info,
                    'knowledge_capability': True,
                },
            )
            self._record_corrective_trace_if_needed(payload.collection_name, corrective_info)
            self.trace.record(
                'query_completed',
                {
                    'collection_name': payload.collection_name,
                    'retrieved_count': len(citations),
                    'latency_ms': latency_ms,
                    'retrieval_question': retrieval_questions[0][:200],
                    'retrieval_questions': [item[:200] for item in retrieval_questions[:6]],
                    'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                    'use_multi_query': payload.use_multi_query,
                    'multi_query': multi_query_info,
                    'use_multi_rewrite': payload.use_multi_rewrite,
                    'multi_rewrite': multi_rewrite_info,
                    'use_hyde': payload.use_hyde,
                    'hyde': hyde_info,
                    'use_long_context_reorder': payload.use_long_context_reorder,
                    'use_parent_chunk_retrieval': payload.use_parent_chunk_retrieval,
                    'use_question_oriented_index': payload.use_question_oriented_index,
                    'use_corrective_rag': payload.use_corrective_rag,
                    **self._graph_trace_flags(payload),
                    'answer_mode': answer_mode,
                    'corrective_rag': corrective_info,
                    'use_context_compression': compression_info['enabled'],
                    'context_compression': compression_info,
                    'semantic_cache': cache_info,
                    'guardrails': self._public_guardrail_state(
                        guardrail_state,
                        citation_redaction,
                        answer_redaction,
                    ),
                    'knowledge_capability': True,
                },
            )
            return response
        citations = self._retrieve_citations(payload, retrieval_questions, answer_question)
        citations, citation_redaction = self._sanitize_citations(citations, payload, 'query')
        contexts, compression_info = self._prepare_answer_context(answer_question, citations, payload)
        prompt_question = guardrail_state['sanitized_question']
        prompt = self.answer_service.build_qa_prompt(
            prompt_question,
            contexts,
            use_guardrails=guardrail_state['prompt_guardrails_enabled'],
        )
        self.trace.record('query_prompt', {'prompt_preview': prompt[:400]})

        corrective_info = self._empty_corrective_info()
        answer_mode = 'no_context'
        answer_redaction = self._empty_redaction_state(self._use_pii_redaction(payload))
        if citations:
            answer, answer_mode = self._generate_answer_with_mode(
                question=prompt_question,
                prompt=prompt,
                citations=citations,
                collection_name=payload.collection_name,
            )
            answer, answer_mode, corrective_info = self._maybe_apply_corrective_rag(
                payload=payload,
                question=prompt_question,
                answer=answer,
                answer_mode=answer_mode,
                citations=citations,
                collection_name=payload.collection_name,
            )
            answer, answer_redaction = self._sanitize_text(answer, payload, target='answer', trace_context='query')
        else:
            answer = '未找到足够依据来回答该问题，请尝试补充文档、放宽筛选条件或换一种问法。'

        latency_ms = int((perf_counter() - started) * 1000)
        response = QueryResponse(
            answer=answer,
            citations=citations,
            retrieved_count=len(citations),
            latency_ms=latency_ms,
            session_id=payload.session_id,
        )
        self._store_semantic_cache(
            payload,
            question=cache_question,
            cache_mode=cache_mode,
            response=response,
            answer_mode=answer_mode,
            metadata={
                'retrieval_questions': retrieval_questions[:6],
                'corrective_rag': corrective_info,
                'context_compression': compression_info,
            },
        )
        self.trace.record(
            'query_completed',
            {
                'collection_name': payload.collection_name,
                'retrieved_count': len(citations),
                'latency_ms': latency_ms,
                'retrieval_question': retrieval_questions[0][:200],
                'retrieval_questions': [item[:200] for item in retrieval_questions[:6]],
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                'use_multi_query': payload.use_multi_query,
                'multi_query': multi_query_info,
                'use_multi_rewrite': payload.use_multi_rewrite,
                'multi_rewrite': multi_rewrite_info,
                'use_hyde': payload.use_hyde,
                'hyde': hyde_info,
                'use_long_context_reorder': payload.use_long_context_reorder,
                'use_parent_chunk_retrieval': payload.use_parent_chunk_retrieval,
                'use_question_oriented_index': payload.use_question_oriented_index,
                'use_corrective_rag': payload.use_corrective_rag,
                **self._graph_trace_flags(payload),
                'answer_mode': answer_mode,
                'corrective_rag': corrective_info,
                'use_context_compression': compression_info['enabled'],
                'context_compression': compression_info,
                'semantic_cache': cache_info,
                'guardrails': self._public_guardrail_state(guardrail_state, citation_redaction, answer_redaction),
            },
        )
        return response

    def _generate_answer(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> str:
        """优先使用 LLM 基于压缩上下文生成答案，失败时回退到本地拼装答案。"""
        answer, _ = self._generate_answer_with_mode(question, prompt, citations, collection_name)
        return answer

    def _generate_answer_with_mode(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str]:
        """生成答案并返回对应模式。"""
        return self.answer_service.generate_answer_with_mode(question, prompt, citations, collection_name)

    def _can_use_knowledge_capability_for_grounded_answer(
        self,
        payload: QueryRequest,
        retrieval_questions: list[str],
    ) -> bool:
        """判断当前场景是否可直接使用 Knowledge Capability 生成受控答案。"""
        return (
            self._can_use_knowledge_capability_for_retrieval(
                payload,
                retrieval_questions,
                self._effective_filters(payload),
            )
        )

    def _grounded_answer_via_knowledge_capability(
        self,
        payload: QueryRequest,
        retrieval_questions: list[str],
        answer_question: str,
        trace_context: str,
    ) -> dict[str, Any] | None:
        """通过 Knowledge Capability 直接生成 grounded answer 与引用结果。"""
        if not self._can_use_knowledge_capability_for_grounded_answer(payload, retrieval_questions):
            return None
        grounded = self.knowledge_capability.grounded_answer(
            GroundedAnswerRequest(
                question=answer_question,
                retrieval_query=retrieval_questions[0],
                collection_name=payload.collection_name,
                top_k=payload.top_k,
                strategy=GroundedAnswerStrategy(
                    use_corrective_rag=payload.use_corrective_rag,
                    use_graph_rag=payload.use_graph_rag,
                    use_hybrid_retrieval=payload.use_hybrid_retrieval,
                    use_rerank=payload.use_rerank,
                    graph_max_hops=payload.graph_max_hops,
                ),
            ),
            trace_context={
                'collection_name': payload.collection_name,
                'trace_context': trace_context,
                'retrieval_question': retrieval_questions[0],
                'retrieval_questions': retrieval_questions[:6],
                'answer_question': answer_question,
                'trace_recorder': self.trace,
            },
        )
        citations = self._citations_from_evidence_pack(grounded.evidence_pack)
        citations, citation_redaction = self._sanitize_citations(citations, payload, trace_context)
        contexts, compression_info = self._prepare_answer_context(answer_question, citations, payload)
        answer, answer_redaction = self._sanitize_text(
            grounded.answer,
            payload,
            target='answer',
            trace_context=trace_context,
        )
        return {
            'answer': answer,
            'citations': citations,
            'answer_mode': grounded.quality_report.final_mode or 'knowledge_capability_grounded',
            'citation_redaction': citation_redaction,
            'answer_redaction': answer_redaction,
            'compression_info': compression_info,
            'contexts': contexts,
            'corrective_info': grounded.quality_report.model_dump(mode='json'),
        }

    def _record_corrective_trace_if_needed(
        self,
        collection_name: str,
        corrective_info: dict[str, Any],
    ) -> None:
        """在启用纠错式 RAG 时补充记录质量检查相关 trace。"""
        if not corrective_info.get('enabled'):
            return
        result = 'accepted' if corrective_info.get('supported') else 'corrected'
        self.trace.record(
            'corrective_rag_checked',
            {'collection_name': collection_name, 'result': result, **corrective_info},
        )

    def _citations_from_evidence_pack(self, evidence_pack) -> list[CitationItem]:
        """把 EvidencePack 中的证据项转换为 QueryResponse 使用的引用模型。"""
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

    def _chat_with_managed_retrieval(
        self,
        payload: ChatRequest,
        mode: str,
        guardrail_state: dict[str, Any] | None = None,
    ) -> QueryResponse:
        """在回退模式下复用统一检索链路完成会话回答。"""
        started = perf_counter()
        question = payload.question.strip()
        guardrail_state = guardrail_state or self._check_guardrails(question, payload, f'chat:{mode}')
        session = self._get_or_create_session(payload.session_id)
        session['messages'].append(self._message('user', self._question_for_storage(question, guardrail_state)))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)

        if guardrail_state['blocked']:
            answer = self._guardrail_block_message()
            session['messages'].append(self._message('assistant', answer))
            session['updated_at'] = datetime.now(timezone.utc)
            self._save_session(payload.session_id)
            self._auto_summarize_session(payload.session_id)
            latency_ms = int((perf_counter() - started) * 1000)
            self.trace.record(
                'chat_completed',
                {
                    'collection_name': payload.collection_name,
                    'session_id': payload.session_id,
                    'retrieved_count': 0,
                    'latency_ms': latency_ms,
                    'chat_mode': mode,
                    'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                    **self._graph_trace_flags(payload),
                    'answer_mode': 'guardrail_blocked',
                    'guardrails': self._public_guardrail_state(guardrail_state),
                },
            )
            return QueryResponse(
                answer=answer,
                citations=[],
                retrieved_count=0,
                latency_ms=latency_ms,
                session_id=payload.session_id,
            )

        retrieval_question = self._prepare_retrieval_question(
            self._build_chat_question(payload.session_id),
            payload.use_query_rewrite,
            f'chat:{mode}',
        )
        response = self._run_query(
            payload,
            retrieval_question=retrieval_question,
            answer_question=question,
            guardrail_state=guardrail_state,
            cache_mode=f'chat_{mode}',
        )
        session['messages'].append(self._message('assistant', response.answer))
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(payload.session_id)
        self._auto_summarize_session(payload.session_id)
        latency_ms = int((perf_counter() - started) * 1000)
        self.trace.record(
            'chat_completed',
            {
                'collection_name': payload.collection_name,
                'session_id': payload.session_id,
                'retrieved_count': len(response.citations),
                'latency_ms': latency_ms,
                'chat_mode': mode,
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                **self._graph_trace_flags(payload),
                'guardrails': self._public_guardrail_state(guardrail_state),
            },
        )
        return response.model_copy(update={'latency_ms': latency_ms})

    def _fallback_chat(self, payload: ChatRequest) -> QueryResponse:
        """保留旧接口语义的本地回退封装。"""
        return self._chat_with_managed_retrieval(payload, mode='local_fallback')

    def _prepare_retrieval_question(
        self,
        question: str,
        use_query_rewrite: bool,
        trace_context: str,
    ) -> str:
        """根据配置决定是否对检索问题做改写。"""
        return self.preprocess_service.prepare_retrieval_question(question, use_query_rewrite, trace_context)

    def _resolve_rewrite_info(
        self,
        question: str,
        use_query_rewrite: bool,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """返回改写后的问题及其详细元信息。"""
        return self.preprocess_service.resolve_rewrite_info(question, use_query_rewrite, trace_context)

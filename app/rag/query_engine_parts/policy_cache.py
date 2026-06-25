"""`query_engine.py` 的策略、脱敏与缓存子模块。

负责 guardrails、PII 脱敏、权限过滤归并和语义缓存上下文构造，
把请求级策略裁决从问答编排中拆出来。
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

from app.core.config import Settings
from app.models.query import ChatRequest, CitationItem, QueryRequest, QueryResponse
from app.models.session import SessionDetail, SessionMessage, SessionSummaryItem, SessionSummaryResponse
from app.rag.llamaindex_components import build_llm, build_metadata_filters, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.retrieval import RagRetrievalService
from app.services.answer_service import AnswerService
from app.services.query_preprocess_service import QueryPreprocessService
from app.services.semantic_cache import SemanticCacheService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.rag.query_engine_parts._typing import QueryEngineTypingMixin
from app.types import MetadataFilters as MetadataFiltersMap, SSEEvent, SessionMessageRecord, SessionRecord

class QueryEnginePolicyCacheMixin(QueryEngineTypingMixin):
    """封装查询引擎中的请求策略、权限过滤与语义缓存辅助逻辑。"""

    def _use_prompt_guardrails(self, payload: QueryRequest) -> bool:
        """返回本次请求是否启用 Prompt Guardrails。"""
        return self.preprocess_service.use_prompt_guardrails(payload)

    def _use_pii_redaction(self, payload: QueryRequest) -> bool:
        """返回本次请求是否启用脱敏。"""
        return self.preprocess_service.use_pii_redaction(payload)

    def _check_guardrails(self, question: str, payload: QueryRequest, trace_context: str) -> dict[str, Any]:
        """检查输入护栏并在需要时完成问题脱敏。"""
        return self.preprocess_service.check_guardrails(question, payload, trace_context)

    def _empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        """返回统一的脱敏结果结构。"""
        return self.preprocess_service.empty_redaction_state(enabled)

    def _sanitize_text(
        self,
        text: str,
        payload: QueryRequest,
        target: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any]]:
        """按请求配置对文本执行脱敏。"""
        return self.preprocess_service.sanitize_text(text, payload, target, trace_context)

    def _sanitize_citations(
        self,
        citations: list[CitationItem],
        payload: QueryRequest,
        trace_context: str,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """对引用片段内容执行脱敏，并汇总命中情况。"""
        return self.preprocess_service.sanitize_citations(citations, payload, trace_context)

    def _question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        """返回写入会话历史时使用的问题文本。"""
        return self.preprocess_service.question_for_storage(question, guardrail_state)

    def _guardrail_block_message(self) -> str:
        """返回统一的护栏拦截提示。"""
        return self.preprocess_service.guardrail_block_message()

    def _public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成可安全暴露给 trace / SSE 的护栏状态。"""
        return self.preprocess_service.public_guardrail_state(
            guardrail_state,
            citation_redaction,
            answer_redaction,
        )

    def _build_semantic_cache_strategy_signature(self, payload: QueryRequest, cache_mode: str) -> str:
        """为影响答案生成的关键开关构建稳定签名。"""
        signature = {
            'cache_mode': cache_mode,
            'collection_name': payload.collection_name,
            'top_k': payload.top_k,
            'use_query_rewrite': payload.use_query_rewrite,
            'use_multi_query': payload.use_multi_query,
            'multi_query_count': payload.multi_query_count,
            'use_multi_rewrite': payload.use_multi_rewrite,
            'multi_rewrite_count': payload.multi_rewrite_count,
            'use_hybrid_retrieval': payload.use_hybrid_retrieval,
            'use_rerank': payload.use_rerank,
            'use_hyde': payload.use_hyde,
            'use_long_context_reorder': payload.use_long_context_reorder,
            'use_context_compression': self._use_context_compression(payload),
            'context_compression_max_chunks': self.settings.context_compression_max_chunks,
            'context_compression_max_sentences': self.settings.context_compression_max_sentences,
            'context_compression_max_chars': self.settings.context_compression_max_chars,
            'use_parent_chunk_retrieval': payload.use_parent_chunk_retrieval,
            'use_question_oriented_index': payload.use_question_oriented_index,
            'use_corrective_rag': payload.use_corrective_rag,
            'use_graph_rag': payload.use_graph_rag,
            'graph_max_hops': payload.graph_max_hops,
            'graph_top_k': payload.graph_top_k,
            'graph_entity_types': payload.graph_entity_types or [],
            'use_prompt_guardrails': self._use_prompt_guardrails(payload),
            'use_pii_redaction': self._use_pii_redaction(payload),
            'llm_model': self.settings.llm_model,
            'llm_runtime': self.llm.__class__.__name__ if self.llm is not None else 'local_fallback',
            'embed_model': self.settings.embed_model,
        }
        return json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def _build_semantic_cache_context(self, payload: QueryRequest, question: str, cache_mode: str) -> str | None:
        """为多轮场景构建上下文签名文本，避免跨上下文误命中。"""
        if payload.session_id is None or not cache_mode.startswith('chat'):
            return None
        return self._build_chat_cache_context(payload.session_id, pending_question=question)

    def _build_chat_cache_context(self, session_id: str, pending_question: str | None = None) -> str:
        """基于会话摘要和最近用户问题构建缓存上下文。"""
        session = self.state.sessions.get(session_id) or self._empty_session()
        summary = session.get('summary')
        recent_questions = [
            str(message.get('content') or '').strip()
            for message in session.get('messages', [])
            if message.get('role') == 'user' and str(message.get('content') or '').strip()
        ]
        if pending_question:
            normalized_pending = pending_question.strip()
            if normalized_pending and (not recent_questions or recent_questions[-1] != normalized_pending):
                recent_questions.append(normalized_pending)
        parts: list[str] = []
        if summary:
            parts.append(f'会话摘要：{summary}')
        parts.extend(recent_questions[-3:])
        return '\n'.join(item for item in parts if item).strip()

    def _lookup_semantic_cache(
        self,
        payload: QueryRequest,
        question: str,
        cache_mode: str,
    ) -> tuple[QueryResponse | None, dict[str, Any]]:
        """按当前请求上下文尝试命中语义缓存。"""
        if self.semantic_cache is None:
            return None, {'enabled': False, 'hit': False, 'reason': 'service_unavailable'}
        filters = self._effective_filters(payload)
        entry, info = self.semantic_cache.lookup(
            collection_name=payload.collection_name,
            mode=cache_mode,
            question=question,
            filters=filters,
            strategy_signature=self._build_semantic_cache_strategy_signature(payload, cache_mode),
            context_signature=self._build_semantic_cache_context(payload, question, cache_mode),
        )
        if entry is None:
            return None, info
        citations = [CitationItem.model_validate(item) for item in entry.get('citations', [])]
        return (
            QueryResponse(
                answer=str(entry.get('answer') or ''),
                citations=citations,
                retrieved_count=len(citations),
                latency_ms=0,
                session_id=payload.session_id,
            ),
            info,
        )

    def _store_semantic_cache(
        self,
        payload: QueryRequest,
        question: str,
        cache_mode: str,
        response: QueryResponse,
        answer_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把已完成响应写入语义缓存。"""
        if self.semantic_cache is None:
            return
        # 缓存写入时同时记录策略签名和上下文签名，避免不同检索策略或不同会话串用答案。
        self.semantic_cache.store(
            collection_name=payload.collection_name,
            mode=cache_mode,
            question=question,
            filters=self._effective_filters(payload),
            strategy_signature=self._build_semantic_cache_strategy_signature(payload, cache_mode),
            context_signature=self._build_semantic_cache_context(payload, question, cache_mode),
            answer=response.answer,
            answer_mode=answer_mode,
            citations=[item.model_dump(mode='json') for item in response.citations],
            source_doc_ids=self._source_doc_ids_from_citations(response.citations),
            metadata=metadata or {},
        )

    def _source_doc_ids_from_citations(self, citations: list[CitationItem]) -> list[str]:
        """尽量从 chunk id 或文件路径反推引用来源文档。"""
        doc_ids: list[str] = []
        seen: set[str] = set()
        chunk_pattern = re.compile(r'^(doc-[A-Za-z0-9]+)-(?:segment|query|parent)-')
        for citation in citations:
            doc_id: str | None = None
            match = chunk_pattern.match(citation.chunk_id)
            if match:
                doc_id = match.group(1)
            elif citation.file_path:
                doc_id = next(
                    (
                        record['doc_id']
                        for record in self.state.documents.values()
                        if record.get('file_path') == citation.file_path
                    ),
                    None,
                )
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                doc_ids.append(doc_id)
        return doc_ids

    def _build_blocked_query_response(
        self,
        payload: QueryRequest,
        guardrail_state: dict[str, Any],
        started: float | None = None,
    ) -> QueryResponse:
        """生成被护栏拦截的 query 响应。"""
        begin = started if started is not None else perf_counter()
        answer = self._guardrail_block_message()
        latency_ms = int((perf_counter() - begin) * 1000)
        self.trace.record(
            'query_completed',
            {
                'collection_name': payload.collection_name,
                'retrieved_count': 0,
                'latency_ms': latency_ms,
                'answer_mode': 'guardrail_blocked',
                'corrective_rag': self._empty_corrective_info(),
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

    def _build_blocked_chat_response(
        self,
        payload: ChatRequest,
        guardrail_state: dict[str, Any],
        mode: str,
    ) -> QueryResponse:
        """生成被护栏拦截的 chat 响应并落会话。"""
        started = perf_counter()
        session = self._get_or_create_session(payload.session_id)
        question = payload.question.strip()
        session['messages'].append(self._message('user', self._question_for_storage(question, guardrail_state)))
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

    def _effective_filters(self, payload: QueryRequest) -> MetadataFiltersMap | None:
        """合并业务过滤条件与请求级权限边界。"""
        merged = dict(payload.filters or {})
        permission_filter = self._effective_permission_filter(payload)
        if permission_filter is None:
            return merged or None

        existing_permission = merged.get('permission')
        if existing_permission is None:
            merged['permission'] = permission_filter
            return merged

        merged_permission = self._merge_permission_filter(existing_permission, permission_filter)
        if merged_permission is None:
            merged['permission'] = {'in': []}
        else:
            merged['permission'] = merged_permission
        return merged

    def _graph_trace_flags(self, payload: QueryRequest) -> dict[str, Any]:
        """返回 GraphRAG 相关的统一 trace 字段。"""
        return {
            'use_graph_rag': payload.use_graph_rag,
            'graph_max_hops': payload.graph_max_hops,
            'graph_top_k': payload.graph_top_k,
            'graph_entity_types': payload.graph_entity_types or [],
        }

    def _effective_permission_filter(self, payload: QueryRequest) -> dict[str, list[str]] | None:
        """把权限范围或允许列表收敛成统一的 permission in 过滤结构。"""
        allowed = self._resolve_allowed_permissions(payload)
        if not allowed:
            return None
        return {'in': allowed}

    def _resolve_allowed_permissions(self, payload: QueryRequest) -> list[str]:
        """解析请求可见权限集合，显式列表优先于范围。"""
        if payload.allowed_permissions:
            return self._normalize_permission_list(payload.allowed_permissions)
        if payload.permission_scope:
            return self._permissions_up_to_scope(payload.permission_scope)
        return []

    def _merge_permission_filter(self, base: Any, boundary: dict[str, list[str]]) -> dict[str, list[str]] | None:
        """把已有 permission filter 与请求级边界做交集。"""
        base_values = self._permission_values_from_filter(base)
        boundary_values = self._permission_values_from_filter(boundary)
        if boundary_values is None:
            return None
        if base_values is None:
            return {'in': boundary_values}
        merged_values = [item for item in boundary_values if item in set(base_values)]
        if not merged_values:
            return None
        return {'in': merged_values}

    def _permission_values_from_filter(self, value: Any) -> list[str] | None:
        """把各种 permission 表达形式统一展开为标准权限列表。"""
        if value is None:
            return None
        if isinstance(value, dict):
            if 'in' in value:
                return self._normalize_permission_list(value.get('in'))
            eq_value = value.get('eq')
            if eq_value is not None:
                return self._normalize_permission_list([eq_value])
            return None
        if isinstance(value, list):
            return self._normalize_permission_list(value)
        return self._normalize_permission_list([value])

    def _normalize_permission_list(self, values: Any) -> list[str]:
        """标准化权限列表并保持声明顺序去重。"""
        if values is None:
            return []
        items = values if isinstance(values, list) else [values]
        ordered: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = self.retrieval_service._normalize_permission(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    def _permissions_up_to_scope(self, scope: Any) -> list[str]:
        """把权限范围展开为可见权限集合。"""
        normalized_scope = self.retrieval_service._normalize_permission(scope)
        if not normalized_scope:
            return []
        hierarchy = ['public', 'internal', 'private', 'restricted', 'confidential']
        if normalized_scope not in hierarchy:
            return [normalized_scope]
        upper_index = hierarchy.index(normalized_scope)
        return hierarchy[: upper_index + 1]

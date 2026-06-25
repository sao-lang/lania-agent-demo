"""`query_engine.py` 的流式输出子模块。

集中处理 SSE 事件流、流式答案拼装和中途检索状态曝光，
让主引擎保留面向调用方的入口和同步编排逻辑。
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

from app.capabilities.knowledge import KnowledgeSearchRequest
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

class QueryEngineStreamingMixin(QueryEngineTypingMixin):
    """封装查询引擎中的 SSE 事件流编排逻辑。"""

    def _stream_query_events(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        stream_mode: str,
        cache_mode: str,
        rewrite_info: dict[str, Any] | None = None,
        guardrail_state: dict[str, Any] | None = None,
    ) -> Iterator[SSEEvent]:
        """把检索与生成过程拆解为可逐步消费的流式事件。"""
        started = perf_counter()
        guardrail_state = guardrail_state or self._check_guardrails(answer_question, payload, stream_mode)
        assert guardrail_state is not None
        # 先推送 start 事件，让客户端尽早获知本次流式请求的上下文。
        yield {
            'event': 'start',
            'data': {
                'mode': stream_mode,
                'collection_name': payload.collection_name,
                'session_id': payload.session_id,
                'use_query_rewrite': payload.use_query_rewrite,
                'use_multi_query': payload.use_multi_query,
                'multi_query_count': payload.multi_query_count,
                'use_multi_rewrite': payload.use_multi_rewrite,
                'multi_rewrite_count': payload.multi_rewrite_count,
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                'use_rerank': payload.use_rerank,
                'use_hyde': payload.use_hyde,
                'use_long_context_reorder': payload.use_long_context_reorder,
                'use_parent_chunk_retrieval': payload.use_parent_chunk_retrieval,
                'use_question_oriented_index': payload.use_question_oriented_index,
                'use_corrective_rag': payload.use_corrective_rag,
                **self._graph_trace_flags(payload),
                'use_context_compression': self._use_context_compression(payload),
                'guardrails': self._public_guardrail_state(guardrail_state),
            },
        }
        if guardrail_state['blocked']:
            answer = self._guardrail_block_message()
            yield {
                'event': 'answer_started',
                'data': {
                    'retrieved_count': 0,
                    'has_citations': False,
                },
            }
            for chunk in self._chunk_text_for_stream(answer):
                yield {'event': 'delta', 'data': {'delta': chunk}}
            latency_ms = int((perf_counter() - started) * 1000)
            guardrails_public = self._public_guardrail_state(guardrail_state)
            yield {
                'event': 'answer_completed',
                'data': {
                    'answer_length': len(answer),
                    'answer_mode': 'guardrail_blocked',
                    'retrieved_count': 0,
                    'corrective_rag': self._empty_corrective_info(),
                    'guardrails': guardrails_public,
                },
            }
            response = QueryResponse(
                answer=answer,
                citations=[],
                retrieved_count=0,
                latency_ms=latency_ms,
                session_id=payload.session_id,
            )
            self.trace.record(
                'query_completed',
                {
                    'collection_name': payload.collection_name,
                    'retrieved_count': 0,
                    'latency_ms': latency_ms,
                    'stream_mode': stream_mode,
                    'answer_mode': 'guardrail_blocked',
                    'corrective_rag': self._empty_corrective_info(),
                    'guardrails': guardrails_public,
                },
            )
            yield {
                'event': 'done',
                'data': {
                    'response': response.model_dump(mode='json'),
                },
            }
            return
        if rewrite_info is not None:
            yield {
                'event': 'rewrite',
                'data': {
                    'original_query': rewrite_info['original_query'],
                    'normalized_query': rewrite_info['normalized_query'],
                    'rewritten_query': rewrite_info['rewritten_query'],
                    'applied_rules': rewrite_info['applied_rules'],
                    'expanded_terms': rewrite_info['expanded_terms'],
                    'changed': rewrite_info['changed'],
                },
            }

        try:
            cache_question = self._question_for_storage(answer_question, guardrail_state)
            retrieval_questions, multi_rewrite_info = self._maybe_apply_multi_rewrite(
                payload=payload,
                retrieval_question=retrieval_question,
                answer_question=answer_question,
                trace_context=stream_mode,
            )
            if multi_rewrite_info is not None:
                yield {'event': 'multi_rewrite', 'data': multi_rewrite_info}
            multi_query_questions, multi_query_info = self._maybe_apply_multi_query(
                payload=payload,
                retrieval_question=retrieval_questions[0],
                answer_question=answer_question,
                trace_context=stream_mode,
            )
            if multi_query_info is not None:
                yield {'event': 'multi_query', 'data': multi_query_info}
            if payload.use_multi_query and multi_query_info is not None and multi_query_info.get('enabled'):
                retrieval_questions = multi_query_questions
            retrieval_question, hyde_info = self._maybe_apply_hyde(
                payload=payload,
                retrieval_question=retrieval_questions[0],
                answer_question=answer_question,
                trace_context=stream_mode,
            )
            if hyde_info is not None:
                yield {'event': 'hyde', 'data': hyde_info}
            if retrieval_questions:
                retrieval_questions = [retrieval_question, *retrieval_questions[1:]]
            else:
                retrieval_questions = [retrieval_question]
            if payload.use_hyde and hyde_info is not None and hyde_info.get('enabled'):
                retrieval_questions = [retrieval_question]
            cached_response, cache_info = self._lookup_semantic_cache(payload, cache_question, cache_mode)
            if cached_response is not None:
                citations = cached_response.citations
                contexts, compression_info = self._prepare_answer_context(answer_question, citations, payload)
                yield {'event': 'cache_hit', 'data': cache_info}
                yield {
                    'event': 'retrieval',
                    'data': {
                        'retrieved_count': len(citations),
                        'retrieval_question': retrieval_questions[0],
                        'retrieval_questions': retrieval_questions[:6],
                        'citations': [item.model_dump(mode='json') for item in citations],
                        'context_compression': compression_info,
                        'guardrails': self._public_guardrail_state(guardrail_state),
                    },
                }
                yield {
                    'event': 'citation_ready',
                    'data': {
                        'retrieved_count': len(citations),
                        'citations': self._stream_citation_snapshot(citations),
                    },
                }
                yield {
                    'event': 'answer_started',
                    'data': {
                        'retrieved_count': len(citations),
                        'has_citations': bool(citations),
                    },
                }
                for chunk in self._chunk_text_for_stream(cached_response.answer):
                    yield {'event': 'delta', 'data': {'delta': chunk}}
                latency_ms = int((perf_counter() - started) * 1000)
                yield {
                    'event': 'answer_completed',
                    'data': {
                        'answer_length': len(cached_response.answer),
                        'answer_mode': 'semantic_cache_hit',
                        'retrieved_count': len(citations),
                        'corrective_rag': self._empty_corrective_info(),
                        'guardrails': self._public_guardrail_state(guardrail_state),
                    },
                }
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
                        'stream_mode': stream_mode,
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
                response = cached_response.model_copy(update={'latency_ms': latency_ms})
                yield {
                    'event': 'done',
                    'data': {
                        'response': response.model_dump(mode='json'),
                    },
                }
                return
            grounded_result = self._grounded_answer_via_knowledge_capability(
                payload=payload,
                retrieval_questions=retrieval_questions,
                answer_question=guardrail_state['sanitized_question'],
                trace_context=stream_mode,
            )
            if grounded_result is not None:
                citations = grounded_result['citations']
                answer = grounded_result['answer']
                answer_mode = grounded_result['answer_mode']
                corrective_info = grounded_result['corrective_info']
                compression_info = grounded_result['compression_info']
                citation_redaction = grounded_result['citation_redaction']
                answer_redaction = grounded_result['answer_redaction']
                yield {
                    'event': 'retrieval',
                    'data': {
                        'retrieved_count': len(citations),
                        'retrieval_question': retrieval_questions[0],
                        'retrieval_questions': retrieval_questions[:6],
                        'citations': [item.model_dump(mode='json') for item in citations],
                        'context_compression': compression_info,
                        'guardrails': self._public_guardrail_state(guardrail_state, citation_redaction),
                    },
                }
                yield {
                    'event': 'citation_ready',
                    'data': {
                        'retrieved_count': len(citations),
                        'citations': self._stream_citation_snapshot(citations),
                    },
                }
                yield {
                    'event': 'answer_started',
                    'data': {
                        'retrieved_count': len(citations),
                        'has_citations': bool(citations),
                    },
                }
                if corrective_info.get('enabled'):
                    yield {'event': 'corrective_check', 'data': corrective_info}
                for chunk in self._chunk_text_for_stream(answer):
                    yield {'event': 'delta', 'data': {'delta': chunk}}
                latency_ms = int((perf_counter() - started) * 1000)
                yield {
                    'event': 'answer_completed',
                    'data': {
                        'answer_length': len(answer),
                        'answer_mode': answer_mode,
                        'retrieved_count': len(citations),
                        'corrective_rag': corrective_info,
                        'guardrails': self._public_guardrail_state(
                            guardrail_state,
                            citation_redaction,
                            answer_redaction,
                        ),
                    },
                }
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
                        'stream_mode': stream_mode,
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
                yield {'event': 'done', 'data': {'response': response.model_dump(mode='json')}}
                return
            citations = self._retrieve_citations(payload, retrieval_questions, answer_question)
            citations, citation_redaction = self._sanitize_citations(citations, payload, stream_mode)
            contexts, compression_info = self._prepare_answer_context(answer_question, citations, payload)
            prompt_question = guardrail_state['sanitized_question']
            prompt = self.answer_service.build_qa_prompt(
                prompt_question,
                contexts,
                use_guardrails=guardrail_state['prompt_guardrails_enabled'],
            )
            self.trace.record('query_prompt', {'prompt_preview': prompt[:400]})
            yield {
                'event': 'retrieval',
                'data': {
                    'retrieved_count': len(citations),
                    'retrieval_question': retrieval_questions[0],
                    'retrieval_questions': retrieval_questions[:6],
                    'citations': [item.model_dump(mode='json') for item in citations],
                    'context_compression': compression_info,
                    'guardrails': self._public_guardrail_state(guardrail_state, citation_redaction),
                },
            }
            yield {
                'event': 'citation_ready',
                'data': {
                    'retrieved_count': len(citations),
                    'citations': self._stream_citation_snapshot(citations),
                },
            }

            if citations:
                yield {
                    'event': 'answer_started',
                    'data': {
                        'retrieved_count': len(citations),
                        'has_citations': True,
                    },
                }
                if payload.use_corrective_rag:
                    # Corrective RAG 需要先拿到完整答案做一次自检，因此这里不直接透传底层流式输出。
                    raw_answer, raw_answer_mode = self._generate_answer_with_mode(
                        question=prompt_question,
                        prompt=prompt,
                        citations=citations,
                        collection_name=payload.collection_name,
                    )
                    answer, answer_mode, corrective_info = self._maybe_apply_corrective_rag(
                        payload=payload,
                        question=prompt_question,
                        answer=raw_answer,
                        answer_mode=raw_answer_mode,
                        citations=citations,
                        collection_name=payload.collection_name,
                    )
                    answer, answer_redaction = self._sanitize_text(
                        answer,
                        payload,
                        target='answer',
                        trace_context=stream_mode,
                    )
                    yield {'event': 'corrective_check', 'data': corrective_info}
                    for chunk in self._chunk_text_for_stream(answer):
                        yield {'event': 'delta', 'data': {'delta': chunk}}
                else:
                    answer, answer_mode, answer_redaction = yield from self._stream_answer(
                        question=prompt_question,
                        prompt=prompt,
                        citations=citations,
                        collection_name=payload.collection_name,
                        payload=payload,
                        trace_context=stream_mode,
                    )
                    corrective_info = self._empty_corrective_info()
            else:
                yield {
                    'event': 'answer_started',
                    'data': {
                        'retrieved_count': 0,
                        'has_citations': False,
                    },
                }
                answer = '未找到足够依据来回答该问题，请尝试补充文档、放宽筛选条件或换一种问法。'
                for chunk in self._chunk_text_for_stream(answer):
                    yield {'event': 'delta', 'data': {'delta': chunk}}
                answer_mode = 'no_context'
                corrective_info = self._empty_corrective_info()
                answer_redaction = self._empty_redaction_state(self._use_pii_redaction(payload))

            yield {
                'event': 'answer_completed',
                'data': {
                    'answer_length': len(answer),
                    'answer_mode': answer_mode,
                    'retrieved_count': len(citations),
                    'corrective_rag': corrective_info,
                    'guardrails': self._public_guardrail_state(guardrail_state, citation_redaction, answer_redaction),
                },
            }

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
                    'stream_mode': stream_mode,
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
                    'stream_mode': stream_mode,
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
            yield {
                'event': 'done',
                'data': {
                    'response': response.model_dump(mode='json'),
                },
            }
        except Exception as exc:
            self.trace.record(
                'query_stream_failed',
                {
                    'collection_name': payload.collection_name,
                    'session_id': payload.session_id,
                    'reason': str(exc),
                    'stream_mode': stream_mode,
                },
            )
            yield {
                'event': 'error',
                'data': {
                    'code': 'stream_failed',
                    'message': str(exc),
                },
            }

    def _maybe_apply_multi_query(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict | None]:
        """根据配置决定是否生成多查询检索候选，并返回对应事件载荷。"""
        return self.preprocess_service.maybe_apply_multi_query(
            payload,
            retrieval_question,
            answer_question,
            trace_context,
        )

    def _retrieve_citations(
        self,
        payload: QueryRequest,
        retrieval_questions: list[str],
        answer_question: str,
    ) -> list[CitationItem]:
        """统一调度单路/多路检索，并在需要时开启父子块扩展。"""
        effective_filters = self._effective_filters(payload)
        if self._can_use_knowledge_capability_for_retrieval(payload, retrieval_questions, effective_filters):
            evidence_pack = self.knowledge_capability.retrieve_evidence(
                KnowledgeSearchRequest(
                    query=retrieval_questions[0],
                    collection_name=payload.collection_name,
                    top_k=payload.top_k,
                    use_graph_rag=payload.use_graph_rag,
                    use_hybrid_retrieval=payload.use_hybrid_retrieval,
                    use_rerank=payload.use_rerank,
                    graph_max_hops=payload.graph_max_hops,
                ),
                trace_context={
                    'collection_name': payload.collection_name,
                    'retrieval_question': retrieval_questions[0],
                    'retrieval_questions': retrieval_questions[:6],
                    'answer_question': answer_question,
                },
            )
            return self._citations_from_evidence_pack(evidence_pack)
        if len(retrieval_questions) >= 2 and hasattr(self.retrieval_service, 'retrieve_multi'):
            kwargs: dict[str, Any] = {
                'collection_name': payload.collection_name,
                'questions': retrieval_questions,
                'anchor_question': answer_question,
                'top_k': payload.top_k,
                'filters': effective_filters,
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                'use_rerank': payload.use_rerank,
                'use_long_context_reorder': payload.use_long_context_reorder,
            }
            if payload.use_parent_chunk_retrieval:
                kwargs['use_parent_chunk_retrieval'] = True
            if payload.use_question_oriented_index:
                kwargs['use_question_oriented_index'] = True
            if payload.use_graph_rag:
                kwargs['use_graph_rag'] = True
                kwargs['graph_max_hops'] = payload.graph_max_hops
                kwargs['graph_top_k'] = payload.graph_top_k
                if payload.graph_entity_types:
                    kwargs['graph_entity_types'] = payload.graph_entity_types
            return self.retrieval_service.retrieve_multi(**kwargs)

        kwargs = {
            'collection_name': payload.collection_name,
            'question': retrieval_questions[0],
            'top_k': payload.top_k,
            'filters': effective_filters,
            'use_hybrid_retrieval': payload.use_hybrid_retrieval,
            'use_rerank': payload.use_rerank,
            'use_long_context_reorder': payload.use_long_context_reorder,
        }
        if payload.use_parent_chunk_retrieval:
            kwargs['use_parent_chunk_retrieval'] = True
        if payload.use_question_oriented_index:
            kwargs['use_question_oriented_index'] = True
        if payload.use_graph_rag:
            kwargs['use_graph_rag'] = True
            kwargs['graph_max_hops'] = payload.graph_max_hops
            kwargs['graph_top_k'] = payload.graph_top_k
            if payload.graph_entity_types:
                kwargs['graph_entity_types'] = payload.graph_entity_types
        return self.retrieval_service.retrieve(**kwargs)

    def _can_use_knowledge_capability_for_retrieval(
        self,
        payload: QueryRequest,
        retrieval_questions: list[str],
        effective_filters,
    ) -> bool:
        """判断当前流式检索流程是否可直接走 Knowledge Capability。"""
        return (
            len(retrieval_questions) == 1
            and not effective_filters
            and not payload.use_multi_query
            and not payload.use_multi_rewrite
            and not payload.use_hyde
            and not payload.use_long_context_reorder
            and not payload.use_parent_chunk_retrieval
            and not payload.use_question_oriented_index
        )

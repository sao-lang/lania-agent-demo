"""RAG 检索主入口。

该模块负责组织检索阶段的主流程，包括稠密召回、词法召回、GraphRAG 召回、多查询融合、
重排、长上下文重组和父块回填等高层策略。更细粒度的候选召回实现、过滤解释、
查询改写细节以及 cross-encoder 运行时已经拆到 `retrieval_parts`，从而把
“检索流程控制”和“检索细节实现”明确分层。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from llama_index.core import VectorStoreIndex

from app.core.config import Settings
from app.models.query import CitationItem
from app.rag.llamaindex_components import build_embed_model, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.state import InMemoryState
from app.types import MetadataFilters as MetadataFiltersMap

from app.rag.retrieval_parts.runtime_retrievers import RetrievalRuntimeMixin
from app.rag.retrieval_parts.filters_queries import RetrievalFilterQueryMixin

# 为旧测试和 patch 点保留模块级导出，实际实现已经下沉到 `retrieval_parts.runtime_retrievers`。
__all__ = ['RagRetrievalService', 'build_vector_store', 'build_embed_model', 'VectorStoreIndex']


class RagRetrievalService(RetrievalRuntimeMixin, RetrievalFilterQueryMixin):
    """组合稠密检索、词法检索和重排，生成最终引用片段。"""

    PARENT_CONTEXT_MAX_CHARS = 1800
    QUERY_FILLER_TERMS = (
        '请问',
        '麻烦',
        '帮我',
        '帮忙',
        '一下',
        '一下子',
        '看看',
        '看下',
        '告诉我',
        '我想知道',
    )
    QUERY_REWRITE_SYNONYMS = {
        '怎么': '如何',
        '咋': '如何',
        '怎样': '如何',
        '查看': '查看',
        '看': '查看',
        '改动': '变更',
        '更新': '增量更新',
        '删掉': '删除',
        '会话': 'session',
        '聊天记忆': '多轮对话上下文',
        '对话历史': '多轮对话上下文',
        '知识库': 'collection',
        '重建': '重建索引',
    }
    DOMAIN_HINTS = {
        'session summary': ('会话摘要', 'summary'),
        'summary': ('会话摘要',),
        'session_id': ('session id',),
        'session': ('会话',),
        'sse': ('流式输出', 'stream'),
        'stream': ('流式输出',),
        'rerank': ('重排',),
        'cross encoder': ('cross-encoder', '重排'),
        'cross-encoder': ('cross encoder', '重排'),
        'ragas': ('评测', 'evaluation'),
        'eval': ('评测', 'evaluation'),
        'query': ('检索问答',),
        'chat': ('多轮对话',),
        'api': ('接口', 'endpoint'),
        'endpoint': ('接口',),
        'embedding': ('向量嵌入',),
        'llm': ('大模型',),
        'collection': ('知识库',),
        'document': ('文档',),
        'documents': ('文档',),
        'reindex': ('重建索引',),
        'feedback': ('反馈',),
        'citation': ('引用',),
        'citations': ('引用',),
    }

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        vector_store: ChromaClientFactory,
        trace: TraceRecorder,
        graph_service: GraphService | None = None,
    ) -> None:
        """初始化检索服务及可选的图谱检索依赖。

        Args:
            settings: 全局配置对象，决定检索能力开关和模型配置。
            state: 内存态业务数据，用于辅助检索策略读取文档信息。
            vector_store: 向量库访问封装，用于执行召回和父块回填。
            trace: 链路追踪记录器，用于上报检索阶段观测数据。
            graph_service: 可选的图谱服务；启用 GraphRAG 时用于扩展图关系召回。
        """
        self.settings = settings
        self.state = state
        self.vector_store = vector_store
        self.trace = trace
        self.graph_service = graph_service
        self.embed_model = build_embed_model(settings)
        self.cross_encoder: Any | None = None
        self.cross_encoder_error: str | None = None
        self.cross_encoder_load_attempted = False

    def retrieve(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters: MetadataFiltersMap | None = None,
        use_hybrid_retrieval: bool = False,
        use_rerank: bool = True,
        use_long_context_reorder: bool = False,
        use_parent_chunk_retrieval: bool = False,
        use_question_oriented_index: bool = False,
        use_graph_rag: bool = False,
        graph_max_hops: int = 1,
        graph_top_k: int | None = None,
        graph_entity_types: list[str] | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> list[CitationItem]:
        """执行检索、融合和重排，返回最终引用列表。

        Args:
            collection_name: 目标知识库名称。
            question: 检索问题文本。
            top_k: 最终返回的引用数量上限。
            filters: 可选的元数据过滤条件。
            use_hybrid_retrieval: 是否同时启用词法召回与向量召回。
            use_rerank: 是否对召回结果执行重排。
            use_long_context_reorder: 是否对长上下文结果做“首尾夹心”重组。
            use_parent_chunk_retrieval: 是否把命中的子块扩展回父块上下文。
            use_question_oriented_index: 是否允许命中 query-hint 等辅助索引。
            use_graph_rag: 是否启用图谱增强检索。
            graph_max_hops: 图谱扩展最大跳数。
            graph_top_k: 图谱召回候选数量上限；为空时跟随 `top_k`。
            graph_entity_types: 图谱检索允许的实体类型白名单。
            trace_context: 额外透传到链路追踪中的上下文字段。

        Returns:
            经融合、重排和上下文扩展后的最终引用列表。
        """
        citations, trace_payload = self._retrieve_once(
            collection_name=collection_name,
            question=question,
            top_k=top_k,
            filters=filters,
            use_hybrid_retrieval=use_hybrid_retrieval,
            use_rerank=use_rerank,
            use_long_context_reorder=use_long_context_reorder,
            use_parent_chunk_retrieval=use_parent_chunk_retrieval,
            use_question_oriented_index=use_question_oriented_index,
            use_graph_rag=use_graph_rag,
            graph_max_hops=graph_max_hops,
            graph_top_k=graph_top_k,
            graph_entity_types=graph_entity_types,
            trace_context=trace_context,
        )
        self.trace.record('retrieval', trace_payload)
        return citations

    def retrieve_multi(
        self,
        collection_name: str,
        questions: list[str],
        anchor_question: str,
        top_k: int,
        filters: MetadataFiltersMap | None = None,
        use_hybrid_retrieval: bool = False,
        use_rerank: bool = True,
        use_long_context_reorder: bool = False,
        use_parent_chunk_retrieval: bool = False,
        use_question_oriented_index: bool = False,
        use_graph_rag: bool = False,
        graph_max_hops: int = 1,
        graph_top_k: int | None = None,
        graph_entity_types: list[str] | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> list[CitationItem]:
        """执行多查询检索，并在查询级别完成候选融合。

        Args:
            collection_name: 目标知识库名称。
            questions: 多个待执行的检索问题列表。
            anchor_question: 作为最终重排锚点的主问题。
            top_k: 最终返回的引用数量上限。
            filters: 可选的元数据过滤条件。
            use_hybrid_retrieval: 是否同时启用词法召回与向量召回。
            use_rerank: 是否在多查询融合后执行最终重排。
            use_long_context_reorder: 是否对最终结果做长上下文重组。
            use_parent_chunk_retrieval: 是否对最终命中做父块回填。
            use_question_oriented_index: 是否允许命中 query-hint 等辅助索引。
            use_graph_rag: 是否启用图谱增强检索。
            graph_max_hops: 图谱扩展最大跳数。
            graph_top_k: 图谱召回候选数量上限；为空时跟随 `top_k`。
            graph_entity_types: 图谱检索允许的实体类型白名单。
            trace_context: 额外透传到链路追踪中的上下文字段。

        Returns:
            经过多查询融合后的最终引用列表。
        """
        queries = [str(item).strip() for item in questions if str(item).strip()]
        if not queries:
            queries = [anchor_question.strip()]

        per_query_candidates: list[list[CitationItem]] = []
        per_query_snapshots: list[dict] = []
        for query in queries:
            # 多查询阶段先关闭重排与长上下文改写，避免把不同 query 的噪声过早放大。
            candidates, payload = self._retrieve_once(
                collection_name=collection_name,
                question=query,
                top_k=top_k,
                filters=filters,
                use_hybrid_retrieval=use_hybrid_retrieval,
                use_rerank=False,
                use_long_context_reorder=False,
                use_parent_chunk_retrieval=False,
                use_question_oriented_index=use_question_oriented_index,
                use_graph_rag=use_graph_rag,
                graph_max_hops=graph_max_hops,
                graph_top_k=graph_top_k,
                graph_entity_types=graph_entity_types,
                trace_context=trace_context,
            )
            per_query_candidates.append(candidates)
            per_query_snapshots.append(
                {
                    'query_preview': query[:160],
                    'hits': len(candidates),
                    'retrieval_mode': payload.get('retrieval_mode'),
                }
            )

        fused_candidates = self._fuse_multi_query_candidates(per_query_candidates)
        reranked, rerank_mode = self._apply_rerank(anchor_question, fused_candidates, use_rerank)
        reordered = self._apply_long_context_reorder(reranked, use_long_context_reorder)
        # 父块回填放在最终排序之后，确保扩展上下文只作用于已经选中的高质量命中。
        citations, parent_chunk_info = self._apply_parent_chunk_retrieval(
            collection_name=collection_name,
            citations=reordered[:top_k],
            enabled=use_parent_chunk_retrieval,
        )

        self.trace.record(
            'retrieval_multi',
            {
                'collection_name': collection_name,
                'query_count': len(queries),
                'queries': [query[:160] for query in queries],
                'top_k': top_k,
                'hits': len(citations),
                'filters': filters or {},
                'use_hybrid_retrieval': use_hybrid_retrieval,
                'use_rerank': use_rerank,
                'rerank_mode': rerank_mode,
                'use_long_context_reorder': use_long_context_reorder,
                'use_parent_chunk_retrieval': use_parent_chunk_retrieval,
                'use_question_oriented_index': use_question_oriented_index,
                'use_graph_rag': use_graph_rag,
                'graph_max_hops': graph_max_hops,
                'graph_top_k': graph_top_k,
                'graph_entity_types': graph_entity_types or [],
                'parent_chunk': parent_chunk_info,
                'per_query': per_query_snapshots,
                'post_rerank': self._rank_snapshot(reranked[:top_k]),
                'post_reorder': self._rank_snapshot(reordered[:top_k]),
                **(trace_context or {}),
            },
        )
        return citations

    def _retrieve_once(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters: MetadataFiltersMap | None,
        use_hybrid_retrieval: bool,
        use_rerank: bool,
        use_long_context_reorder: bool,
        use_parent_chunk_retrieval: bool,
        use_question_oriented_index: bool,
        use_graph_rag: bool,
        graph_max_hops: int,
        graph_top_k: int | None,
        graph_entity_types: list[str] | None,
        trace_context: dict[str, Any] | None = None,
    ) -> tuple[list[CitationItem], dict]:
        """执行单次检索主流程，并返回结果与追踪载荷。

        该方法是检索阶段的核心编排点，统一串起过滤条件处理、不同召回源融合、
        目标块聚合、重排、长上下文重组和父块回填。

        Returns:
            第一项为最终引用列表，第二项为用于 `trace.record()` 的观测载荷。
        """
        # query-hint 等扩展字段只在显式开启时参与过滤，避免默认检索被辅助索引噪声放大。
        effective_filters = self._effective_retrieval_filters(filters, use_question_oriented_index)
        dense_ranked = self._retrieve_dense(collection_name, question, top_k, effective_filters)
        lexical_ranked = (
            self._retrieve_lexical(collection_name, question, top_k, effective_filters)
            if use_hybrid_retrieval
            else []
        )
        graph_ranked, graph_info = self._retrieve_graph(
            collection_name=collection_name,
            question=question,
            top_k=graph_top_k or top_k,
            filters=effective_filters,
            use_graph_rag=use_graph_rag,
            graph_max_hops=graph_max_hops,
            graph_entity_types=graph_entity_types,
        )
        initial_ranked, retrieval_mode = self._fuse_candidates(
            dense_ranked=dense_ranked,
            lexical_ranked=lexical_ranked,
            graph_ranked=graph_ranked,
            use_hybrid_retrieval=use_hybrid_retrieval,
            use_graph_rag=use_graph_rag,
        )
        aggregated = self._aggregate_target_hits(initial_ranked)
        # 先做目标聚合再重排，避免同一文档的多个近似命中在重排阶段互相抢占名额。
        reranked, rerank_mode = self._apply_rerank(question, aggregated, use_rerank)
        reordered = self._apply_long_context_reorder(reranked, use_long_context_reorder)
        citations, parent_chunk_info = self._apply_parent_chunk_retrieval(
            collection_name=collection_name,
            citations=reordered[:top_k],
            enabled=use_parent_chunk_retrieval,
        )
        trace_payload = {
            'collection_name': collection_name,
            'top_k': top_k,
            'hits': len(citations),
            'filters': filters or {},
            'effective_filters': effective_filters or {},
            'query': question,
            'use_hybrid_retrieval': use_hybrid_retrieval,
            'retrieval_mode': retrieval_mode,
            'dense_candidates': len(dense_ranked),
            'lexical_candidates': len(lexical_ranked),
            'graph_candidates': len(graph_ranked),
            'graph': graph_info,
            'use_rerank': use_rerank,
            'rerank_mode': rerank_mode,
            'use_long_context_reorder': use_long_context_reorder,
            'use_parent_chunk_retrieval': use_parent_chunk_retrieval,
            'use_question_oriented_index': use_question_oriented_index,
            'use_graph_rag': use_graph_rag,
            'graph_max_hops': graph_max_hops,
            'graph_top_k': graph_top_k or top_k,
            'graph_entity_types': graph_entity_types or [],
            'parent_chunk': parent_chunk_info,
            'dense_ranked': self._rank_snapshot(dense_ranked[:top_k]),
            'lexical_ranked': self._rank_snapshot(lexical_ranked[:top_k]),
            'graph_ranked': self._rank_snapshot(graph_ranked[:top_k]),
            'pre_rerank': self._rank_snapshot(initial_ranked[:top_k]),
            'post_aggregate': self._rank_snapshot(aggregated[:top_k]),
            'post_rerank': self._rank_snapshot(reranked[:top_k]),
            'post_reorder': self._rank_snapshot(reordered[:top_k]),
            **(trace_context or {}),
        }
        return citations, trace_payload

    def _apply_parent_chunk_retrieval(
        self,
        collection_name: str,
        citations: list[CitationItem],
        enabled: bool,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """把命中的子块扩展回父块上下文，形成 small-to-big 生成输入。

        Args:
            collection_name: 目标知识库名称。
            citations: 当前检索链路筛出的候选引用。
            enabled: 是否启用父块回填。

        Returns:
            第一项为扩展后的引用列表，第二项为记录扩展效果的观测指标。
        """
        metrics: dict[str, Any] = {
            'enabled': enabled and bool(citations),
            'expanded': 0,
            'deduplicated': 0,
            'source': 'disabled',
        }
        if not citations or not enabled:
            return citations, metrics

        try:
            collection = self.vector_store.get_or_create_collection(collection_name)
            payload = collection.get(ids=[item.chunk_id for item in citations], include=['metadatas'])
        except Exception as exc:
            self.trace.record(
                'parent_chunk_retrieval_failed',
                {'collection_name': collection_name, 'reason': str(exc)},
            )
            metrics.update({'enabled': False, 'source': f'failed:{str(exc)}'})
            return citations, metrics

        ids = payload.get('ids') or []
        metadatas = payload.get('metadatas') or []
        metadata_by_id = {str(chunk_id): (metadata or {}) for chunk_id, metadata in zip(ids, metadatas)}
        # 先收集父块 ID，再尝试批量加载父文档内容，避免逐条查询造成额外开销。
        parent_ids = [
            str(metadata.get('parent_chunk_id') or '').strip()
            for metadata in metadata_by_id.values()
            if str(metadata.get('parent_chunk_id') or '').strip()
        ]
        parent_docs_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        if parent_ids:
            try:
                parent_payload = collection.get(ids=sorted(set(parent_ids)), include=['documents', 'metadatas'])
                parent_doc_ids = parent_payload.get('ids') or []
                parent_documents = parent_payload.get('documents') or []
                parent_metadatas = parent_payload.get('metadatas') or []
                parent_docs_by_id = {
                    str(parent_id): (str(document or '').strip(), metadata or {})
                    for parent_id, document, metadata in zip(parent_doc_ids, parent_documents, parent_metadatas)
                    if str(document or '').strip()
                }
            except Exception as exc:
                self.trace.record(
                    'parent_document_lookup_failed',
                    {'collection_name': collection_name, 'reason': str(exc)},
                )

        expanded: list[CitationItem] = []
        seen_parent_ids: set[str] = set()
        deduplicated = 0
        expanded_count = 0
        parent_doc_hits = 0

        for citation in citations:
            metadata = metadata_by_id.get(citation.chunk_id, {})
            parent_chunk_id = str(metadata.get('parent_chunk_id') or citation.chunk_id)
            if parent_chunk_id in seen_parent_ids:
                deduplicated += 1
                continue
            seen_parent_ids.add(parent_chunk_id)

            parent_document_text, parent_document_metadata = parent_docs_by_id.get(parent_chunk_id, ('', {}))
            parent_context = parent_document_text or str(metadata.get('parent_context') or '').strip()
            if not parent_context:
                expanded.append(citation)
                continue

            clipped = parent_context[: self.PARENT_CONTEXT_MAX_CHARS].strip()
            expanded_count += 1
            if parent_document_text:
                parent_doc_hits += 1
            expanded.append(
                citation.model_copy(
                    update={
                        'text': clipped,
                        'child_chunk_id': citation.child_chunk_id or citation.chunk_id,
                        'parent_chunk_id': parent_chunk_id,
                        'context_scope': 'parent',
                        'section_title': parent_document_metadata.get('section_title') or metadata.get('section_title'),
                        'hierarchy_path': parent_document_metadata.get('hierarchy_path') or metadata.get('hierarchy_path'),
                    }
                )
            )

        metrics.update(
            {
                'expanded': expanded_count,
                'deduplicated': deduplicated,
                'parent_document_hits': parent_doc_hits,
                'source': 'parent_documents' if parent_doc_hits else 'metadata_parent_context',
            }
        )
        return expanded, metrics

    def _effective_retrieval_filters(
        self,
        filters: MetadataFiltersMap | None,
        use_question_oriented_index: bool,
    ) -> MetadataFiltersMap:
        """叠加内部索引过滤条件，控制是否纳入 query-hint 子索引。

        Args:
            filters: 来自上层调用方的显式过滤条件。
            use_question_oriented_index: 是否允许命中问题导向型辅助索引。

        Returns:
            合并内部检索约束后的最终过滤条件字典。
        """
        effective = dict(filters or {})
        if 'index_kind' in effective:
            return effective
        effective['index_kind'] = ['content', 'query_hint', 'title_summary'] if use_question_oriented_index else 'content'
        return effective

    def _aggregate_target_hits(self, citations: list[CitationItem]) -> list[CitationItem]:
        """按真实目标块聚合同一内容的多向量命中。

        当同一目标块通过 content、query_hint、title_summary 等多个向量入口被命中时，
        这里会合并这些命中，并为多路命中结果增加适度分数奖励。
        """
        if not citations:
            return citations
        grouped: dict[str, list[CitationItem]] = defaultdict(list)
        for citation in citations:
            grouped[citation.chunk_id].append(citation)

        aggregated: list[CitationItem] = []
        for group in grouped.values():
            best = max(group, key=lambda item: item.score or 0.0)
            matched_via = []
            seen_via: set[str] = set()
            for item in group:
                for marker in item.matched_via or [item.index_kind or 'content']:
                    if marker in seen_via:
                        continue
                    matched_via.append(marker)
                    seen_via.add(marker)
            bonus = 0.04 * max(0, len(matched_via) - 1)
            if len({item.index_kind for item in group if item.index_kind}) > 1:
                bonus += 0.03
            aggregated.append(
                best.model_copy(
                    update={
                        'score': round((best.score or 0.0) + bonus, 4),
                        'matched_via': matched_via,
                    }
                )
            )
        return sorted(aggregated, key=lambda item: item.score or 0.0, reverse=True)

    def _fuse_multi_query_candidates(self, ranked_groups: list[list[CitationItem]]) -> list[CitationItem]:
        """使用简化版 RRF 融合多查询候选结果。

        Args:
            ranked_groups: 每个查询对应的一组有序候选结果。

        Returns:
            按融合分数重新排序后的候选引用列表。
        """
        rank_constant = 60
        scores: dict[str, float] = {}
        citations: dict[str, CitationItem] = {}

        for ranked in ranked_groups:
            for rank, citation in enumerate(ranked, start=1):
                key = self._candidate_key(citation)
                scores[key] = scores.get(key, 0.0) + 1.0 / (rank_constant + rank)
                existing = citations.get(key)
                if existing is None or (citation.score or 0.0) > (existing.score or 0.0):
                    citations[key] = citation

        fused = [citation.model_copy(update={'score': round(scores[key], 4)}) for key, citation in citations.items()]
        return sorted(fused, key=lambda item: item.score or 0.0, reverse=True)

    def _apply_long_context_reorder(
        self, citations: list[CitationItem], enabled: bool
    ) -> list[CitationItem]:
        """对长上下文结果执行首尾交错重排。

        该策略用于缓解长列表上下文中间位置更容易被模型忽略的问题。
        """
        if not enabled or len(citations) <= 2:
            return citations

        head: list[CitationItem] = []
        tail: list[CitationItem] = []
        for index, citation in enumerate(citations):
            if index % 2 == 0:
                head.append(citation)
            else:
                tail.insert(0, citation)
        return head + tail

    def rewrite_query(self, question: str) -> str:
        """仅返回改写后的查询文本。

        Args:
            question: 原始用户问题。

        Returns:
            规则改写后的检索问题文本。
        """
        return self.rewrite_query_info(question)['rewritten_query']

    def rewrite_query_info(self, question: str) -> dict[str, Any]:
        """返回查询改写结果及对应的规则命中信息。

        Args:
            question: 原始用户问题。

        Returns:
            包含原始问题、标准化结果、改写结果、命中规则和扩展词信息的字典。
        """
        original = question.strip()
        normalized = self._normalize_query_text(original)
        rewritten = normalized
        applied_rules: list[str] = []

        condensed = self._remove_filler_terms(rewritten)
        if condensed != rewritten:
            rewritten = condensed
            applied_rules.append('remove_fillers')

        synonym_rewritten, synonym_rules = self._apply_synonym_replacements(rewritten)
        if synonym_rewritten != rewritten:
            rewritten = synonym_rewritten
            applied_rules.extend(synonym_rules)

        expanded, expanded_terms = self._expand_domain_hints(rewritten)
        if expanded != rewritten:
            rewritten = expanded
            applied_rules.append('expand_domain_terms')

        deduplicated = self._deduplicate_query_terms(rewritten)
        if deduplicated != rewritten:
            rewritten = deduplicated
            applied_rules.append('deduplicate_terms')

        if not rewritten:
            rewritten = normalized or original

        return {
            'original_query': original,
            'normalized_query': normalized,
            'rewritten_query': rewritten,
            'applied_rules': applied_rules,
            'expanded_terms': expanded_terms,
            'changed': rewritten != original,
        }

    def rewrite_multi_query_info(self, question: str, max_queries: int = 3) -> dict[str, Any]:
        """基于单问题生成多查询检索候选。

        Args:
            question: 原始用户问题。
            max_queries: 最多生成的查询数量，实际范围会被限制在 2 到 6 之间。

        Returns:
            包含查询列表、生成策略和统计信息的字典。
        """
        original = question.strip()
        base_info = self.rewrite_query_info(original)
        base = str(base_info.get('rewritten_query') or '').strip() or original

        desired = max(2, min(int(max_queries or 3), 6))
        queries: list[str] = []
        strategies: list[dict] = []
        seen: set[str] = set()

        def _add(text: str, kind: str) -> None:
            cleaned = re.sub(r'\s+', ' ', str(text).strip())
            if not cleaned:
                return
            normalized = cleaned.lower()
            if normalized in seen:
                return
            queries.append(cleaned)
            strategies.append({'kind': kind, 'query': cleaned[:200]})
            seen.add(normalized)

        _add(base, 'rewrite_base')

        normalized_original = self._normalize_query_text(original)
        _add(normalized_original, 'original_normalized')

        keyword_query = self._keyword_only_query(base)
        if keyword_query:
            _add(keyword_query, 'keyword_only')

        for segment in self._split_query_segments(base):
            _add(segment, 'segment')
            if len(queries) >= desired:
                break

        if len(queries) < desired:
            reduced = self._drop_generic_terms(base)
            if reduced and reduced != base:
                _add(reduced, 'drop_generic')

        queries = queries[:desired]
        strategies = strategies[: len(queries)]
        return {
            'enabled': True,
            'query_count': len(queries),
            'queries': [item[:200] for item in queries],
            'strategies': strategies,
            'base': base[:200],
        }

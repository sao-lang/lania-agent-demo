"""RAG 系统检索主入口模块。

负责组织稠密召回、词法召回、GraphRAG 召回、多查询融合、重排等高层策略。
与主应用的 `app/rag/retrieval.py` 功能一致，但使用独立配置和状态。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.rag_system.config.settings import RagSettings
from app.rag_system.models.query import CitationItem
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.retrieval.base import RetrievalTypingMixin
from app.rag_system.retrieval.graph_service import RagGraphService
from app.rag_system.retrieval.parts.runtime_retrievers import RetrievalRuntimeMixin
from app.rag_system.retrieval.parts.filters_queries import RetrievalFilterQueryMixin
from app.rag_system.store.state import RagState
from app.rag_system.vector_store.chroma import ChromaClientFactory
from app.rag_system.vector_store.llamaindex_adapter import build_embed_model

MetadataFiltersMap = dict[str, Any]


class RagRetrievalService(RetrievalRuntimeMixin, RetrievalFilterQueryMixin):
    """组合稠密检索、词法检索和重排，生成最终引用片段。"""

    PARENT_CONTEXT_MAX_CHARS = 1800

    def __init__(
        self,
        settings: RagSettings,
        state: RagState,
        vector_store: ChromaClientFactory,
        trace: TraceRecorder,
        graph_service: RagGraphService | None = None,
    ) -> None:
        """初始化检索服务。

        Args:
            settings: RAG 系统配置。
            state: RAG 系统内存状态。
            vector_store: 向量库访问封装。
            trace: 链路追踪记录器。
            graph_service: 可选的图谱服务。
        """
        self.settings = settings
        self.state = state
        self.vector_store = vector_store
        self.trace = trace
        self.graph_service = graph_service
        self.embed_model = build_embed_model(settings)
        self.cross_encoder: Any | None = None

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
            use_hybrid_retrieval: 是否启用混合检索。
            use_rerank: 是否对召回结果执行重排。
            use_long_context_reorder: 是否对长上下文结果做重组。
            use_parent_chunk_retrieval: 是否把命中的子块扩展回父块。
            use_question_oriented_index: 是否允许命中 query-hint。
            use_graph_rag: 是否启用图谱增强检索。
            graph_max_hops: 图谱扩展最大跳数。
            graph_top_k: 图谱召回候选数量上限。
            graph_entity_types: 图谱检索实体类型白名单。
            trace_context: 额外透传的上下文字段。

        Returns:
            经融合、重排后的最终引用列表。
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
        trace_context: dict[str, Any] | None,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """单次检索的核心逻辑。"""
        effective_top_k = top_k * 3  # 先多召回来给重排留空间

        # 1. 向量检索
        dense_citations = self._retrieve_dense(collection_name, question, effective_top_k, filters)

        # 2. 混合检索（可选）
        if use_hybrid_retrieval:
            lexical_citations = self._retrieve_lexical(collection_name, question, effective_top_k, filters)
            all_citations = self._deduplicate(dense_citations + lexical_citations)
            retrieval_mode = 'hybrid'
        else:
            all_citations = dense_citations
            retrieval_mode = 'dense'

        # 3. 图谱检索（可选）
        if use_graph_rag and self.graph_service:
            graph_citations = self._retrieve_graph(collection_name, question, effective_top_k, graph_max_hops, graph_entity_types)
            all_citations = self._deduplicate(all_citations + graph_citations)
            retrieval_mode = 'hybrid_graph'

        # 4. 重排
        reranked, rerank_mode = self._apply_rerank(question, all_citations, use_rerank)

        # 5. 长上下文重组（可选）
        if use_long_context_reorder:
            reordered = self._apply_long_context_reorder(reranked)
        else:
            reordered = reranked

        # 6. 父块回填（可选）
        if use_parent_chunk_retrieval:
            citations, _ = self._apply_parent_chunk_retrieval(collection_name, reordered[:top_k], enabled=True)
        else:
            citations = reordered[:top_k]

        trace_payload = {
            'collection_name': collection_name,
            'question': question[:200],
            'top_k': top_k,
            'hits': len(citations),
            'retrieval_mode': retrieval_mode,
            'rerank_mode': rerank_mode,
            'dense_hits': len(dense_citations),
            'use_hybrid_retrieval': use_hybrid_retrieval,
            'use_rerank': use_rerank,
            'use_graph_rag': use_graph_rag,
            'use_parent_chunk_retrieval': use_parent_chunk_retrieval,
            'use_long_context_reorder': use_long_context_reorder,
            **(trace_context or {}),
        }
        return citations, trace_payload

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
        """执行多查询检索，并在查询级别完成候选融合。"""
        queries = [str(item).strip() for item in questions if str(item).strip()]
        if not queries:
            queries = [anchor_question.strip()]

        per_query_candidates: list[list[CitationItem]] = []
        per_query_snapshots: list[dict] = []
        for query in queries:
            candidates, payload = self._retrieve_once(
                collection_name=collection_name, question=query, top_k=top_k,
                filters=filters, use_hybrid_retrieval=use_hybrid_retrieval,
                use_rerank=False, use_long_context_reorder=False,
                use_parent_chunk_retrieval=False,
                use_question_oriented_index=use_question_oriented_index,
                use_graph_rag=use_graph_rag, graph_max_hops=graph_max_hops,
                graph_top_k=graph_top_k, graph_entity_types=graph_entity_types,
                trace_context=trace_context,
            )
            per_query_candidates.append(candidates)
            per_query_snapshots.append({'query_preview': query[:160], 'hits': len(candidates)})

        fused_candidates = self._fuse_multi_query_candidates(per_query_candidates)
        reranked, rerank_mode = self._apply_rerank(anchor_question, fused_candidates, use_rerank)
        reordered = self._apply_long_context_reorder(reranked, use_long_context_reorder)
        citations, parent_chunk_info = self._apply_parent_chunk_retrieval(
            collection_name=collection_name, citations=reordered[:top_k], enabled=use_parent_chunk_retrieval,
        )

        self.trace.record('retrieval_multi', {
            'collection_name': collection_name, 'query_count': len(queries),
            'top_k': top_k, 'hits': len(citations),
            'rerank_mode': rerank_mode, 'parent_chunk': parent_chunk_info,
            'per_query': per_query_snapshots,
            **(trace_context or {}),
        })
        return citations

    def _apply_long_context_reorder(self, citations: list[CitationItem], enabled: bool = True) -> list[CitationItem]:
        """对长上下文结果做"首尾夹心"重组。"""
        if not enabled or len(citations) <= 3:
            return citations
        mid = len(citations) // 2
        return citations[:mid] + citations[-mid:]

    def _apply_parent_chunk_retrieval(
        self,
        collection_name: str,
        citations: list[CitationItem],
        enabled: bool,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """父块回填：通过 parent_chunk_id 将子块扩展为父块全文。"""
        if not enabled:
            return citations, {'parent_chunk': False}

        expanded_count = 0
        result: list[CitationItem] = []
        # 查找父块：有 parent_chunk_id 的说明是子块，尝试从向量库获取父块全文
        for c in citations:
            parent_id = getattr(c, 'parent_chunk_id', None)
            if parent_id:
                try:
                    collection = self.vector_store.get_or_create_collection(collection_name)
                    parent_data = collection.get(ids=[parent_id], include=['documents', 'metadatas'])
                    if parent_data and parent_data['ids']:
                        parent_text = (parent_data['documents'][0] or '') if parent_data['documents'] else ''
                        if parent_text and len(parent_text) > len(c.text):
                            c.text = parent_text[:self.PARENT_CONTEXT_MAX_CHARS]
                            c.parent_chunk_id = parent_id
                            expanded_count += 1
                except Exception:
                    pass
            result.append(c)

        info = {'parent_chunk': True, 'expanded': expanded_count}
        return result, info

    def _rank_snapshot(self, citations: list[CitationItem]) -> list[float]:
        """返回引用分数快照，用于 trace。"""
        return [c.score or 0 for c in citations]

    def _fuse_multi_query_candidates(self, per_query: list[list[CitationItem]]) -> list[CitationItem]:
        """RRF 多查询候选融合。"""
        if not per_query:
            return []
        k = 60  # RRF 常数
        scores: dict[str, float] = {}
        items: dict[str, CitationItem] = {}
        for rank_list in per_query:
            for rank, c in enumerate(rank_list):
                cid = c.chunk_id
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
                if cid not in items or (c.score or 0) > (items[cid].score or 0):
                    items[cid] = c
        for cid in items:
            items[cid].score = scores[cid]
        result = sorted(items.values(), key=lambda c: c.score or 0, reverse=True)
        return result

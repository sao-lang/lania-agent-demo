"""`retrieval.py` 的候选召回与运行时子模块。

包含重排、dense/lexical/graph 候选召回以及 cross-encoder 运行时加载，
把策略主流程与底层候选生成实现分开。
"""

from __future__ import annotations

import importlib
from importlib.util import find_spec
import math
from typing import Any

from llama_index.core import VectorStoreIndex

from app.models.query import CitationItem
from app.rag.llamaindex_components import build_metadata_filters, build_vector_store
from app.rag.retrieval_parts._typing import RetrievalTypingMixin


class RetrievalRuntimeMixin(RetrievalTypingMixin):
    """封装检索服务中的候选召回、融合与运行时加载逻辑。"""

    def _rerank_score(self, question: str, text: str, base_score: float | None) -> float:
        """基于词项覆盖率和原始分数计算轻量重排得分。"""
        question_tokens = set(self._tokenize(question))
        text_tokens = set(self._tokenize(text))
        overlap = len(question_tokens & text_tokens)
        coverage = overlap / max(len(question_tokens), 1)
        lexical_bonus = min(coverage, 1.0)
        return (base_score or 0.0) * 0.7 + lexical_bonus * 0.3

    def _apply_rerank(
        self,
        question: str,
        citations: list[CitationItem],
        use_rerank: bool,
    ) -> tuple[list[CitationItem], str]:
        """对候选片段执行 cross-encoder 或词法重排。"""
        if not use_rerank:
            return citations, 'disabled'

        # 优先尝试 cross-encoder；不可用时回退到词法重排。
        if self.settings.enable_cross_encoder_rerank:
            cross_encoder = self._get_cross_encoder()
            if cross_encoder is not None:
                try:
                    return self._apply_cross_encoder_rerank(question, citations, cross_encoder), 'cross_encoder'
                except Exception as exc:
                    self.cross_encoder_error = str(exc)
                    self.trace.record(
                        'cross_encoder_rerank_fallback',
                        {
                            'reason': str(exc),
                            'model': self.settings.cross_encoder_model,
                        },
                    )

        reranked: list[CitationItem] = []
        for citation in citations:
            reranked.append(
                citation.model_copy(
                    update={
                        'score': round(
                            self._rerank_score(question, citation.text, citation.score),
                            4,
                        )
                    }
                )
            )
        mode = 'lexical_fallback' if self.settings.enable_cross_encoder_rerank else 'lexical'
        return sorted(reranked, key=lambda item: item.score or 0.0, reverse=True), mode

    def _deduplicate(self, citations: list[CitationItem]) -> list[CitationItem]:
        """按 chunk_id 去重，避免同一片段重复返回。"""
        deduped: dict[str, CitationItem] = {}
        for citation in citations:
            key = self._candidate_key(citation)
            existing = deduped.get(key)
            if existing is None or (citation.score or 0.0) > (existing.score or 0.0):
                deduped[key] = citation
        return list(deduped.values())

    def _retrieve_dense(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[CitationItem]:
        """使用向量检索生成候选片段。"""
        vector_store = build_vector_store(self.vector_store, collection_name)
        index = VectorStoreIndex.from_vector_store(vector_store=vector_store, embed_model=self.embed_model)
        retriever = index.as_retriever(
            similarity_top_k=max(top_k * 4, top_k),
            filters=build_metadata_filters(filters),
        )
        nodes = retriever.retrieve(question)

        candidates: list[CitationItem] = []
        for node_with_score in nodes:
            metadata = node_with_score.node.metadata or {}
            if not self._matches_filters(metadata, filters):
                continue

            # query_hint / parent 等辅助索引命中后，统一回填为最终应展示给用户的目标 chunk。
            chunk_id = str(metadata.get('retrieval_target_chunk_id') or node_with_score.node.node_id)
            text = str(metadata.get('retrieval_target_text') or node_with_score.node.get_content())
            index_kind = self._metadata_text(metadata, 'index_kind')
            node_level = self._metadata_text(metadata, 'node_level')

            candidates.append(
                CitationItem(
                    chunk_id=chunk_id,
                    matched_chunk_id=str(node_with_score.node.node_id),
                    source=self._format_citation_source(metadata),
                    file_path=metadata.get('file_path'),
                    page=metadata.get('page'),
                    score=round(node_with_score.score or 0.0, 4),
                    text=text,
                    index_kind=index_kind,
                    node_level=node_level,
                    matched_via=[index_kind] if index_kind else None,
                    chunking_strategy_requested=self._metadata_text(metadata, 'chunking_strategy_requested'),
                    chunking_strategy_effective=self._metadata_text(metadata, 'chunking_strategy_effective'),
                    chunking_prepared=self._metadata_bool(metadata, 'chunking_prepared'),
                    source_segment_count=self._metadata_int(metadata, 'source_segment_count'),
                    section_title=metadata.get('section_title'),
                    hierarchy_path=metadata.get('hierarchy_path'),
                    source_archive=self._metadata_text(metadata, 'source_archive'),
                    archive_member_path=self._metadata_text(metadata, 'archive_member_path'),
                    archive_member_display_path=self._metadata_text(metadata, 'archive_member_display_path'),
                )
            )

        return sorted(candidates, key=lambda item: item.score or 0.0, reverse=True)

    def _retrieve_lexical(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[CitationItem]:
        """从 Chroma 已存文本中执行基于词项的补充检索。"""
        collection = self.vector_store.get_or_create_collection(collection_name)
        payload = collection.get(include=['documents', 'metadatas'])
        ids = payload.get('ids') or []
        documents = payload.get('documents') or []
        metadatas = payload.get('metadatas') or []

        candidates: list[CitationItem] = []
        for chunk_id, text, metadata in zip(ids, documents, metadatas):
            metadata = metadata or {}
            if not text or not self._matches_filters(metadata, filters):
                continue

            score = self._lexical_retrieval_score(question, text, metadata)
            if score <= 0:
                continue

            # 词法检索与向量检索保持同一目标块语义，方便后续融合与去重。
            resolved_chunk_id = str(metadata.get('retrieval_target_chunk_id') or chunk_id)
            resolved_text = str(metadata.get('retrieval_target_text') or text)
            index_kind = self._metadata_text(metadata, 'index_kind')
            node_level = self._metadata_text(metadata, 'node_level')

            candidates.append(
                CitationItem(
                    chunk_id=resolved_chunk_id,
                    matched_chunk_id=str(chunk_id),
                    source=self._format_citation_source(metadata),
                    file_path=metadata.get('file_path'),
                    page=metadata.get('page'),
                    score=round(score, 4),
                    text=resolved_text,
                    index_kind=index_kind,
                    node_level=node_level,
                    matched_via=[index_kind] if index_kind else None,
                    chunking_strategy_requested=self._metadata_text(metadata, 'chunking_strategy_requested'),
                    chunking_strategy_effective=self._metadata_text(metadata, 'chunking_strategy_effective'),
                    chunking_prepared=self._metadata_bool(metadata, 'chunking_prepared'),
                    source_segment_count=self._metadata_int(metadata, 'source_segment_count'),
                    section_title=metadata.get('section_title'),
                    hierarchy_path=metadata.get('hierarchy_path'),
                    source_archive=self._metadata_text(metadata, 'source_archive'),
                    archive_member_path=self._metadata_text(metadata, 'archive_member_path'),
                    archive_member_display_path=self._metadata_text(metadata, 'archive_member_display_path'),
                )
            )

        ranked = sorted(candidates, key=lambda item: item.score or 0.0, reverse=True)
        return ranked[: max(top_k * 4, top_k)]

    def _retrieve_graph(
        self,
        *,
        collection_name: str,
        question: str,
        top_k: int,
        filters: dict | None,
        use_graph_rag: bool,
        graph_max_hops: int,
        graph_entity_types: list[str] | None,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """执行图谱增强检索，并返回图谱侧观测信息。"""
        if not use_graph_rag:
            return [], {'enabled': False, 'reason': 'disabled'}
        if self.graph_service is None:
            return [], {'enabled': False, 'reason': 'service_unavailable'}
        return self.graph_service.retrieve(
            collection_name=collection_name,
            question=question,
            top_k=top_k,
            max_hops=graph_max_hops,
            filters=filters,
            entity_types=graph_entity_types,
        )

    def _fuse_candidates(
        self,
        dense_ranked: list[CitationItem],
        lexical_ranked: list[CitationItem],
        graph_ranked: list[CitationItem],
        use_hybrid_retrieval: bool,
        use_graph_rag: bool,
    ) -> tuple[list[CitationItem], str]:
        """融合稠密检索、词法检索和图谱检索候选。"""
        if not use_hybrid_retrieval and not use_graph_rag:
            return dense_ranked, 'dense'

        ranked_groups: list[list[CitationItem]] = []
        mode_parts: list[str] = []
        if dense_ranked:
            ranked_groups.append(dense_ranked)
            mode_parts.append('dense')
        if use_hybrid_retrieval and lexical_ranked:
            ranked_groups.append(lexical_ranked)
            mode_parts.append('lexical')
        if use_graph_rag and graph_ranked:
            ranked_groups.append(graph_ranked)
            mode_parts.append('graph')

        if not ranked_groups:
            if use_hybrid_retrieval and use_graph_rag:
                return [], 'hybrid_graph_empty'
            if use_hybrid_retrieval:
                return [], 'hybrid_empty'
            if use_graph_rag:
                return [], 'graph_empty'
            return [], 'dense_empty'

        if len(ranked_groups) == 1:
            if mode_parts == ['lexical']:
                return lexical_ranked, 'hybrid_lexical_only'
            if mode_parts == ['graph']:
                return graph_ranked, 'graph_only'
            if use_hybrid_retrieval:
                return dense_ranked, 'hybrid_dense_only'
            if use_graph_rag:
                return dense_ranked, 'dense_graph_seeded'
            return dense_ranked, 'dense'

        # 使用 RRF 风格分数融合多路结果。
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

        fused = [
            citation.model_copy(update={'score': round(scores[key], 4)})
            for key, citation in citations.items()
        ]
        retrieval_mode = 'hybrid'
        if use_hybrid_retrieval and use_graph_rag:
            retrieval_mode = 'hybrid_graph'
        elif use_graph_rag:
            retrieval_mode = 'dense_graph'
        return sorted(fused, key=lambda item: item.score or 0.0, reverse=True), retrieval_mode

    def _apply_cross_encoder_rerank(
        self,
        question: str,
        citations: list[CitationItem],
        cross_encoder: Any,
    ) -> list[CitationItem]:
        """使用 cross-encoder 对候选片段进行精排。"""
        pairs = [[question, citation.text[:2048]] for citation in citations]
        raw_scores = cross_encoder.predict(pairs)
        reranked: list[CitationItem] = []
        for citation, raw_score in zip(citations, raw_scores):
            score = self._normalize_cross_encoder_score(raw_score, citation.score)
            reranked.append(citation.model_copy(update={'score': round(score, 4)}))
        return sorted(reranked, key=lambda item: item.score or 0.0, reverse=True)

    def _normalize_cross_encoder_score(self, raw_score: Any, base_score: float | None) -> float:
        """把原始 cross-encoder 分数压缩到稳定范围并融合基础得分。"""
        value = float(raw_score[0] if isinstance(raw_score, (list, tuple)) else raw_score)
        sigmoid = 1.0 / (1.0 + math.exp(-max(min(value, 20.0), -20.0)))
        return sigmoid * 0.8 + (base_score or 0.0) * 0.2

    def _get_cross_encoder(self) -> Any | None:
        """按需加载 cross-encoder 模型实例。"""
        if not self.settings.enable_cross_encoder_rerank:
            return None
        if self.cross_encoder is not None:
            return self.cross_encoder
        if self.cross_encoder_load_attempted:
            return None

        self.cross_encoder_load_attempted = True
        try:
            sentence_transformers = importlib.import_module('sentence_transformers')
            CrossEncoder = getattr(sentence_transformers, 'CrossEncoder')
            kwargs = {'model_name': self.settings.cross_encoder_model}
            if self.settings.cross_encoder_device:
                kwargs['device'] = self.settings.cross_encoder_device
            self.cross_encoder = CrossEncoder(**kwargs)
            self.cross_encoder_error = None
            return self.cross_encoder
        except Exception as exc:
            self.cross_encoder_error = str(exc)
            return None

    def get_rerank_runtime_status(self) -> dict[str, Any]:
        """返回重排依赖和运行模式的当前状态。"""
        dependency_available = find_spec('sentence_transformers') is not None
        if self.settings.enable_cross_encoder_rerank:
            if self.cross_encoder is not None:
                runtime_mode = 'cross_encoder'
            elif self.cross_encoder_error:
                runtime_mode = 'lexical_fallback'
            else:
                runtime_mode = 'cross_encoder_pending'
        else:
            runtime_mode = 'lexical'

        return {
            'enabled': self.settings.enable_cross_encoder_rerank,
            'model': self.settings.cross_encoder_model,
            'device': self.settings.cross_encoder_device,
            'dependency_available': dependency_available,
            'runtime_mode': runtime_mode,
            'runtime_class': self.cross_encoder.__class__.__name__ if self.cross_encoder is not None else None,
            'last_error': self.cross_encoder_error,
        }

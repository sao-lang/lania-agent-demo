"""RAG 系统检索运行时模块。

实现稠密检索、词法检索、GraphRAG 检索、重排等高层策略。
与主应用的 `app/rag/retrieval_parts/runtime_retrievers.py` 功能一致。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from llama_index.core import VectorStoreIndex

from app.rag_system.models.query import CitationItem
from app.rag_system.retrieval.base import RetrievalTypingMixin
from app.rag_system.vector_store.llamaindex_adapter import build_metadata_filters, build_vector_store


class RetrievalRuntimeMixin(RetrievalTypingMixin):
    """提供检索运行时能力：稠密、词法、GraphRAG、重排等。"""

    def _rerank_score(self, query: str, text: str, original_score: float) -> float:
        """基于词项覆盖率的轻量重排打分。"""
        query_tokens = set(re.findall(r"[0-9A-Za-z_一-鿿]+", query.lower()))
        if not query_tokens:
            return original_score
        text_tokens = re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower())
        overlap = sum(1 for t in text_tokens if t in query_tokens)
        coverage = overlap / len(query_tokens) if query_tokens else 0
        return 0.7 * original_score + 0.3 * coverage

    def _apply_rerank(self, question: str, citations: list[CitationItem], use_rerank: bool) -> tuple[list[CitationItem], str]:
        """对检索结果执行重排。"""
        if not use_rerank or not citations:
            return citations, 'no_rerank'

        try:
            if self.cross_encoder:
                pairs = [(question, c.text) for c in citations]
                scores = self.cross_encoder.predict(pairs)
                for idx, c in enumerate(citations):
                    c.score = float(scores[idx]) if idx < len(scores) else c.score
                citations.sort(key=lambda c: c.score or 0, reverse=True)
                return citations, 'cross_encoder'
        except Exception:
            pass

        for c in citations:
            c.score = self._rerank_score(question, c.text, c.score or 0)
        citations.sort(key=lambda c: c.score or 0, reverse=True)
        return citations, 'lexical'

    def _deduplicate(self, citations: list[CitationItem]) -> list[CitationItem]:
        """按 chunk_id 去重。"""
        seen: set[str] = set()
        result: list[CitationItem] = []
        for c in citations:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                result.append(c)
        return result

    def _retrieve_dense(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters: Any = None,
    ) -> list[CitationItem]:
        """向量检索。"""
        try:
            vector_store = build_vector_store(self.vector_store, collection_name)
            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.embed_model,
            )
            query_engine = index.as_query_engine(
                similarity_top_k=top_k,
                filters=build_metadata_filters(filters),
            )
            response = query_engine.query(question)
            citations = []
            for node in response.source_nodes:
                score = node.score if hasattr(node, 'score') else None
                meta = node.metadata if hasattr(node, 'metadata') else {}
                c = CitationItem(
                    chunk_id=node.node_id if hasattr(node, 'node_id') else str(id(node)),
                    source=meta.get('file_name', ''),
                    file_path=meta.get('file_path', ''),
                    page=meta.get('page', None),
                    score=float(score) if score is not None else None,
                    text=str(node.text) if hasattr(node, 'text') else '',
                    section_title=meta.get('section_title', None),
                    hierarchy_path=meta.get('hierarchy_path', None),
                )
                citations.append(c)
            return citations
        except Exception:
            return []

    def _retrieve_lexical(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters: Any = None,
    ) -> list[CitationItem]:
        """词项检索。"""
        try:
            chroma_collection = self.vector_store.get_or_create_collection(collection_name)
            all_data = chroma_collection.get(include=['documents', 'metadatas'])
            if not all_data or not all_data['ids']:
                return []

            query_tokens = set(re.findall(r"[0-9A-Za-z_一-鿿]+", question.lower()))
            scored: list[tuple[float, str, str, dict]] = []
            for idx, doc_id in enumerate(all_data['ids']):
                text = (all_data['documents'][idx] or '') if all_data['documents'] else ''
                meta = (all_data['metadatas'][idx] or {}) if all_data['metadatas'] else {}
                score = self._lexical_retrieval_score(question, text, query_tokens, self.DOMAIN_HINTS)
                if score > 0:
                    scored.append((score, doc_id, text, meta))

            scored.sort(key=lambda x: x[0], reverse=True)
            citations = []
            for score, cid, text, meta in scored[:top_k]:
                citations.append(CitationItem(
                    chunk_id=cid,
                    source=meta.get('file_name', ''),
                    file_path=meta.get('file_path', ''),
                    score=score,
                    text=text,
                    section_title=meta.get('section_title', None),
                    hierarchy_path=meta.get('hierarchy_path', None),
                ))
            return citations
        except Exception:
            return []

    def _lexical_retrieval_score(
        self,
        query: str,
        text: str,
        query_tokens: set[str],
        domain_hints: dict[str, tuple[str, ...]],
    ) -> float:
        """计算词项检索得分。"""
        if not text or not query_tokens:
            return 0.0
        text_tokens = re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower())
        if not text_tokens:
            return 0.0
        text_token_set = set(text_tokens)
        overlap = query_tokens & text_token_set
        if not overlap:
            return 0.0

        coverage = len(overlap) / len(query_tokens)
        density = sum(1 for t in text_tokens if t in query_tokens) / len(text_tokens)

        hint_bonus = 0.0
        query_lower = query.lower()
        for hint_key, hint_values in domain_hints.items():
            if hint_key in query_lower:
                for hv in hint_values:
                    if hv.lower() in text.lower():
                        hint_bonus += 0.15
                        break

        return min(coverage * 0.6 + density * 0.4 + hint_bonus, 1.0)

    def _retrieve_graph(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        graph_max_hops: int = 1,
        graph_entity_types: list[str] | None = None,
    ) -> list[CitationItem]:
        """图谱增强检索。"""
        if not self.graph_service:
            return []
        try:
            return self.graph_service.retrieve(
                collection_name=collection_name,
                question=question,
                top_k=top_k,
                max_hops=graph_max_hops,
                entity_types=graph_entity_types,
            )
        except Exception:
            return []

    def _ensure_cross_encoder(self) -> None:
        """延迟加载 cross-encoder。"""
        if self.cross_encoder is not None or getattr(self, '_cross_encoder_load_attempted', False):
            return
        self._cross_encoder_load_attempted = True
        if not self.settings.enable_cross_encoder_rerank:
            self.cross_encoder_error = 'cross_encoder_rerank disabled'
            return
        try:
            from sentence_transformers import CrossEncoder
            model_name = self.settings.cross_encoder_model
            device = self.settings.cross_encoder_device
            kwargs = {'model_name': model_name}
            if device:
                kwargs['device'] = device
            self.cross_encoder = CrossEncoder(**kwargs)
            self.cross_encoder_error = None
        except Exception as exc:
            self.cross_encoder_error = str(exc)
            self.cross_encoder = None

    def _apply_cross_encoder_rerank(self, question: str, citations: list[CitationItem]) -> tuple[list[CitationItem], str]:
        """基于 cross-encoder 的重排。"""
        self._ensure_cross_encoder()
        if not self.cross_encoder:
            return self._apply_rerank(question, citations, True)  # fallback to lexical
        try:
            pairs = [(question, c.text) for c in citations if c.text]
            if not pairs:
                return citations, 'cross_encoder_no_input'
            scores = self.cross_encoder.predict(pairs)
            for idx, c in enumerate(citations):
                if idx < len(scores):
                    c.score = self._normalize_cross_encoder_score(float(scores[idx]))
            citations.sort(key=lambda c: c.score or 0, reverse=True)
            return citations, 'cross_encoder'
        except Exception:
            return self._apply_rerank(question, citations, True)

    def _normalize_cross_encoder_score(self, raw: float) -> float:
        """将 cross-encoder 的原始 sigmoid 输出映射到 [0, 1] 区间。"""
        return max(0.0, min(1.0, (raw + 1.0) / 2.0 if raw < 0 else raw))

    def get_rerank_runtime_status(self) -> dict[str, Any]:
        """返回重排运行状态。"""
        self._ensure_cross_encoder()
        return {
            'cross_encoder_loaded': self.cross_encoder is not None,
            'cross_encoder_error': getattr(self, 'cross_encoder_error', None),
            'cross_encoder_load_attempted': getattr(self, '_cross_encoder_load_attempted', False),
        }

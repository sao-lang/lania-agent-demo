"""RAG 统一门面模块。

把 Knowledge Capability 收敛为稳定的对外入口，供工具层、query runtime 与
后续架构迁移在不感知底层实现差异的前提下复用统一调用接口。
"""


from __future__ import annotations

from time import perf_counter
from typing import Any

from app.capabilities.knowledge import (
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeCapability,
    KnowledgeSearchRequest,
)
from app.models.query import CitationItem, QueryRequest, QueryResponse


class RagFacade:
    """对外暴露稳定的 RAG 门面。"""

    def __init__(self, knowledge: KnowledgeCapability) -> None:
        """初始化 RAG 门面对外依赖的知识能力实现。"""
        self.knowledge = knowledge

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        """加载文档上下文摘要。"""
        return self.knowledge.load_document_context(request)

    def retrieve_evidence(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ):
        """执行证据检索。"""
        return self.knowledge.retrieve_evidence(request, trace_context=trace_context)

    def retrieve_evidence_for_query(
        self,
        *,
        question: str,
        collection_name: str,
        top_k: int,
        use_graph_rag: bool = False,
        use_hybrid_retrieval: bool = False,
        use_rerank: bool = False,
        graph_max_hops: int = 2,
        trace_context: dict[str, Any] | None = None,
    ):
        """为 query runtime 提供稳定的证据检索入口。"""
        return self.retrieve_evidence(
            KnowledgeSearchRequest(
                query=question,
                collection_name=collection_name,
                top_k=top_k,
                use_graph_rag=use_graph_rag,
                use_hybrid_retrieval=use_hybrid_retrieval,
                use_rerank=use_rerank,
                graph_max_hops=graph_max_hops,
            ),
            trace_context=trace_context,
        )

    def grounded_answer(
        self,
        request: GroundedAnswerRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> GroundedAnswerResult:
        """执行 grounded answer。"""
        return self.knowledge.grounded_answer(request, trace_context=trace_context)

    def grounded_query(
        self,
        payload: QueryRequest,
        *,
        retrieval_query: str | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> QueryResponse:
        """把 grounded answer 投影成 query 响应。"""
        started = perf_counter()
        grounded = self.grounded_answer(
            GroundedAnswerRequest(
                question=payload.question.strip(),
                retrieval_query=retrieval_query,
                collection_name=payload.collection_name,
                top_k=payload.top_k,
            ),
            trace_context=trace_context,
        )
        citations = [self._to_citation_item(item) for item in grounded.citations]
        return QueryResponse(
            answer=grounded.answer,
            citations=citations,
            retrieved_count=len(grounded.evidence_pack.evidence_items),
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            session_id=payload.session_id,
        )

    def _to_citation_item(self, item: CitationItem | dict[str, Any]) -> CitationItem:
        """把字典或现成模型统一转换为 CitationItem。"""
        if isinstance(item, CitationItem):
            return item
        return CitationItem.model_validate(item)

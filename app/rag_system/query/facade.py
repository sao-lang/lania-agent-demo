"""RAG 系统统一门面模块。

提供稳定的对外入口，供工具层、API 层在感知不到底层实现差异的前提下复用同一接口。
与主应用的 `app/rag/facade.py` 功能一致，但使用独立组件。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.rag_system.knowledge.base import (
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeSearchRequest,
)
from app.rag_system.knowledge.service import RagKnowledgeCapability
from app.rag_system.models.query import CitationItem, QueryRequest, QueryResponse


class RagFacade:
    """对外暴露稳定的 RAG 门面。"""

    def __init__(self, knowledge: RagKnowledgeCapability) -> None:
        """初始化 RAG 门面。"""
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
        citations = [
            CitationItem(
                chunk_id=c.get('chunk_id', ''),
                source=c.get('source', ''),
                text=c.get('text', ''),
                score=c.get('score'),
            )
            for c in grounded.citations
        ]
        latency_ms = int((perf_counter() - started) * 1000)
        return QueryResponse(
            answer=grounded.answer,
            citations=citations,
            retrieved_count=len(citations),
            latency_ms=latency_ms,
            answer_mode='grounded',
        )

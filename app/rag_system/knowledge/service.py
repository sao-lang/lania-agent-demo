"""RAG 系统 Knowledge 能力默认实现。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.rag_system.knowledge.base import (
    DocumentContextItem,
    DocumentContextRequest,
    DocumentContextResult,
    EvidencePack,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeSearchRequest,
)
from app.rag_system.retrieval.service import RagRetrievalService
from app.rag_system.store.state import RagState
from app.rag_system.vector_store.chroma import ChromaClientFactory


class RagKnowledgeCapability:
    """把 RAG 系统栈适配为统一 Knowledge Capability。"""

    def __init__(
        self,
        state: RagState,
        retrieval: RagRetrievalService,
        vector_store: ChromaClientFactory,
        llm: Any | None = None,
    ) -> None:
        """初始化知识能力。"""
        self.state = state
        self.retrieval = retrieval
        self.vector_store = vector_store
        self.llm = llm

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        """汇总集合内文档的摘要与元数据。"""
        documents: list[DocumentContextItem] = []
        for record in self.state.documents.values():
            if record.get('collection_name') != request.collection_name:
                continue
            if request.doc_ids and record.get('doc_id') not in request.doc_ids:
                continue
            documents.append(
                DocumentContextItem(
                    doc_id=str(record.get('doc_id')),
                    title=str(record.get('document_title') or record.get('file_name') or record.get('doc_id')),
                    summary=str(record.get('document_summary') or '').strip() or '暂无文档摘要。',
                    sections=[],
                    metadata={
                        'file_name': record.get('file_name'),
                        'file_type': record.get('file_type'),
                        'indexed_chunks': record.get('indexed_chunks'),
                    },
                )
            )
        return DocumentContextResult(documents=documents)

    def retrieve_evidence(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> EvidencePack:
        """调用底层检索服务并包装结果。"""
        citations = self.retrieval.retrieve(
            request.collection_name,
            request.query,
            request.top_k,
            use_hybrid_retrieval=request.use_hybrid_retrieval,
            use_rerank=request.use_rerank,
            use_graph_rag=request.use_graph_rag,
            graph_max_hops=request.graph_max_hops,
            trace_context=trace_context,
        )
        items = []
        for c in citations:
            items.append({
                'chunk_id': c.chunk_id,
                'text': c.text,
                'source': c.source,
                'file_path': c.file_path,
                'score': c.score,
                'section_title': c.section_title,
            })
        return EvidencePack(items=items)

    def grounded_answer(
        self,
        request: GroundedAnswerRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> GroundedAnswerResult:
        """基于证据生成回答。"""
        # 1. 检索
        citations = self.retrieval.retrieve(
            request.collection_name,
            request.retrieval_query or request.question,
            request.top_k,
            use_hybrid_retrieval=request.use_hybrid_retrieval,
            use_rerank=request.use_rerank,
            use_graph_rag=request.use_graph_rag,
            graph_max_hops=request.graph_max_hops,
            trace_context=trace_context,
        )

        # 2. 构建 prompt
        from app.rag_system.answer.prompting import build_qa_prompt
        contexts = [c.text for c in citations]
        prompt = build_qa_prompt(request.question, contexts)

        # 3. 生成回答
        answer = '未找到足够依据来回答该问题。'
        if self.llm:
            try:
                response = self.llm.complete(prompt)
                answer = str(response).strip()
            except Exception:
                pass
        elif contexts:
            answer = f"基于找到的 {len(contexts)} 条相关证据，请自行判断。\n\n" + "\n\n".join(contexts)

        result_citations = [
            {'chunk_id': c.chunk_id, 'text': c.text[:200], 'source': c.source, 'score': c.score}
            for c in citations
        ]

        return GroundedAnswerResult(
            answer=answer,
            citations=result_citations,
            grounded=len(citations) > 0,
        )

"""RAG 系统 Knowledge 能力协议和数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class DocumentContextItem:
    """文档上下文条目。"""
    doc_id: str
    title: str
    summary: str
    sections: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentContextRequest:
    """请求文档上下文。"""
    collection_name: str
    doc_ids: list[str] | None = None


@dataclass
class DocumentContextResult:
    """文档上下文结果。"""
    documents: list[DocumentContextItem] = field(default_factory=list)


@dataclass
class KnowledgeSearchRequest:
    """检索请求。"""
    query: str
    collection_name: str
    top_k: int = 5
    doc_ids: list[str] | None = None
    use_graph_rag: bool = False
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    graph_max_hops: int = 1
    focus_aspects: list[str] | None = None


@dataclass
class GroundedAnswerRequest:
    """Grounded answer 请求。"""
    question: str
    collection_name: str
    top_k: int = 5
    retrieval_query: str | None = None
    use_graph_rag: bool = False
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    use_corrective_rag: bool = False
    graph_max_hops: int = 1


@dataclass
class GroundedAnswerResult:
    """Grounded answer 结果。"""
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    grounded: bool = False
    quality_report: dict[str, Any] | None = None


class EvidencePack:
    """证据包。"""
    def __init__(self, items: list[dict[str, Any]] | None = None):
        self.items = items or []

    def __len__(self) -> int:
        return len(self.items)


class KnowledgeCapability(Protocol):
    """Knowledge 能力协议。"""

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        """加载文档上下文摘要。"""
        ...

    def retrieve_evidence(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> EvidencePack:
        """执行证据检索。"""
        ...

    def grounded_answer(
        self,
        request: GroundedAnswerRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> GroundedAnswerResult:
        """执行 grounded answer。"""
        ...

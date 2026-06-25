"""Knowledge 能力契约模块。

定义文档上下文加载、证据检索和 grounded answer 所需的请求/响应模型与统一协议，
为本地 RAG 适配层和远程 provider 共享同一套能力边界。
"""


from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.capabilities.knowledge.contracts import GroundedAnswerStrategy, RetrievalQualityReport
from app.models.artifact import EvidencePack


class DocumentContextItem(BaseModel):
    """单个文档的受控上下文摘要。"""

    doc_id: str
    title: str
    summary: str
    sections: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentContextRequest(BaseModel):
    """文档上下文请求。"""

    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)


class DocumentContextResult(BaseModel):
    """文档上下文结果。"""

    documents: list[DocumentContextItem] = Field(default_factory=list)


class KnowledgeSearchRequest(BaseModel):
    """知识检索请求。"""

    query: str
    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    focus_aspects: list[str] = Field(default_factory=list)
    use_graph_rag: bool = False
    use_hybrid_retrieval: bool = True
    use_rerank: bool = True
    graph_max_hops: int = Field(default=2, ge=1, le=5)


class GroundedAnswerRequest(BaseModel):
    """受控 grounded answer 请求。"""

    question: str
    retrieval_query: str | None = None
    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    focus_aspects: list[str] = Field(default_factory=list)
    strategy: GroundedAnswerStrategy = Field(default_factory=GroundedAnswerStrategy)


class GroundedAnswerResult(BaseModel):
    """grounded answer 结果。"""

    answer: str
    evidence_pack: EvidencePack
    citations: list[dict[str, Any]] = Field(default_factory=list)
    grounded: bool = True
    quality_report: RetrievalQualityReport = Field(default_factory=RetrievalQualityReport)


class DocumentContextCall(BaseModel):
    """Knowledge Capability 文档上下文调用封装。"""

    request: DocumentContextRequest
    trace_context: dict[str, Any] | None = None


class KnowledgeSearchCall(BaseModel):
    """Knowledge Capability 检索调用封装。"""

    request: KnowledgeSearchRequest
    trace_context: dict[str, Any] | None = None


class GroundedAnswerCall(BaseModel):
    """Knowledge Capability grounded answer 调用封装。"""

    request: GroundedAnswerRequest
    trace_context: dict[str, Any] | None = None


class KnowledgeCapability(Protocol):
    """统一知识能力接口。"""

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        """加载指定集合下文档的受控上下文摘要。"""

        ...

    def retrieve_evidence(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> EvidencePack:
        """执行知识检索并返回结构化证据包。"""

        ...

    def grounded_answer(
        self,
        request: GroundedAnswerRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> GroundedAnswerResult:
        """基于检索证据生成带质量信息的 grounded answer。"""

        ...

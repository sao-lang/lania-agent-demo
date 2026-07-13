"""RAG 系统查询模型模块。

定义检索问答请求、聊天请求、引用项和响应体模型。
这些模型与主应用的 ``app.models.query`` 保持一致，但独立于主应用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CitationItem(BaseModel):
    """回答中引用到的检索片段。"""

    chunk_id: str
    source: str
    file_path: str | None = None
    page: int | None = None
    score: float | None = None
    text: str

    matched_chunk_id: str | None = None
    index_kind: str | None = None
    node_level: str | None = None
    matched_via: list[str] | None = None
    child_chunk_id: str | None = None
    parent_chunk_id: str | None = None
    context_scope: str | None = None
    section_title: str | None = None
    hierarchy_path: str | None = None

    source_archive: str | None = None
    archive_member_path: str | None = None
    archive_member_display_path: str | None = None

    graph_path: str | None = None
    graph_relation: str | None = None
    graph_start_entity: str | None = None
    graph_end_entity: str | None = None
    graph_path_hops: int | None = None


class QueryRequest(BaseModel):
    """标准检索问答请求体。"""

    question: str
    collection_name: str
    top_k: int = Field(default=5, ge=1, le=20)
    session_id: str | None = None
    filters: dict | None = None
    permission_scope: str | None = None
    allowed_permissions: list[str] | None = None

    use_prompt_guardrails: bool | None = None
    use_pii_redaction: bool | None = None
    use_query_rewrite: bool = True
    use_multi_query: bool = False
    multi_query_count: int = Field(default=3, ge=2, le=6)
    use_multi_rewrite: bool = False
    multi_rewrite_count: int = Field(default=3, ge=2, le=6)
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    use_hyde: bool = False
    use_long_context_reorder: bool = False
    use_context_compression: bool | None = None
    use_parent_chunk_retrieval: bool = False
    use_question_oriented_index: bool = False
    use_corrective_rag: bool = False
    use_graph_rag: bool = False
    graph_max_hops: int = Field(default=1, ge=1, le=3)
    graph_top_k: int = Field(default=5, ge=1, le=20)
    graph_entity_types: list[str] | None = None


class ChatRequest(BaseModel):
    """多轮对话请求体。"""

    question: str
    collection_name: str
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict | None = None
    permission_scope: str | None = None
    allowed_permissions: list[str] | None = None

    use_prompt_guardrails: bool | None = None
    use_pii_redaction: bool | None = None
    use_query_rewrite: bool = True
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    use_hyde: bool = False
    use_long_context_reorder: bool = False
    use_context_compression: bool | None = None
    use_parent_chunk_retrieval: bool = False
    use_question_oriented_index: bool = False
    use_corrective_rag: bool = False
    use_graph_rag: bool = False
    graph_max_hops: int = Field(default=1, ge=1, le=3)
    graph_top_k: int = Field(default=5, ge=1, le=20)


class QueryResponse(BaseModel):
    """标准检索问答响应体。"""

    answer: str
    citations: list[CitationItem] = Field(default_factory=list)
    retrieved_count: int = 0
    latency_ms: int | None = None
    session_id: str | None = None
    answer_mode: str | None = None
    degraded: bool = False
    retrieval_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryResultArtifactContent(BaseModel):
    """query/chat 的结构化最终交付物。"""

    answer: str
    answer_mode: str
    citations: list[CitationItem] = Field(default_factory=list)
    grounded: bool = False
    degraded: bool = False
    session_id: str | None = None
    retrieval_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

"""RAG 门面工具模块。

这组工具只依赖 ``RagFacade``，为 workflow 和 runtime 提供稳定、统一的
RAG 能力入口，避免上层直接耦合更底层的知识检索实现。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.knowledge import (
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    GroundedAnswerStrategy,
    KnowledgeSearchRequest,
)
from app.models.artifact import EvidencePack
from app.models.query import QueryRequest, QueryResponse
from app.rag.facade import RagFacade


class RagLoadDocumentContextInput(BaseModel):
    """加载文档上下文所需输入。"""

    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)


class RagRetrieveEvidenceInput(BaseModel):
    """检索证据所需输入。"""

    query: str
    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    focus_aspects: list[str] = Field(default_factory=list)


class RagGroundedAnswerInput(BaseModel):
    """生成 grounded answer 所需输入。"""

    question: str
    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    focus_aspects: list[str] = Field(default_factory=list)
    use_graph_rag: bool = False
    use_corrective_rag: bool = False
    retrieval_query: str | None = None


class RagGroundedQueryInput(BaseModel):
    """执行完整 grounded query 所需输入。"""

    question: str
    collection_name: str
    top_k: int = Field(default=5, ge=1, le=20)
    session_id: str | None = None
    retrieval_query: str | None = None
    use_graph_rag: bool = False
    use_corrective_rag: bool = False


class _BaseRagTool:
    """RAG 工具共享基类。"""

    version = 'v1'
    risk_level = 'low'
    sandbox_mode = 'inline'

    def _rag(self, context) -> RagFacade:
        """解析当前上下文中的 RagFacade 依赖。"""

        if context.rag is not None:
            return context.rag
        if context.knowledge is not None:
            return RagFacade(context.knowledge)
        raise ToolExecutionError(
            code='rag_facade_unavailable',
            message='rag facade is not configured',
            error_type='dependency_error',
            default_action='fallback',
        )

    def _trace_context(self, context, *, tool_name: str) -> dict[str, str | None]:
        """为底层 RAG facade 组装统一 trace 上下文。"""

        return {
            'task_id': context.task_id,
            'step_name': context.step_name,
            'tool_name': tool_name,
        }


class RagLoadDocumentContextTool(_BaseRagTool):
    """加载文档上下文的工具封装。"""

    name = 'rag_load_document_context'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=200)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = RagLoadDocumentContextInput
    output_model = DocumentContextResult

    def run(self, payload: RagLoadDocumentContextInput, context) -> DocumentContextResult:
        """读取指定文档集合的上下文摘要。"""

        return self._rag(context).load_document_context(
            DocumentContextRequest(collection_name=payload.collection_name, doc_ids=list(payload.doc_ids))
        )


class RagRetrieveEvidenceTool(_BaseRagTool):
    """检索证据的工具封装，可切换普通 RAG 或图谱 RAG。"""

    name = 'rag_retrieve_evidence'
    timeout_ms = 15000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status', 'error_type']
    input_model = RagRetrieveEvidenceInput
    output_model = EvidencePack

    def __init__(self, use_graph_rag: bool = False) -> None:
        """初始化检索工具并决定是否启用图谱检索路径。"""

        self.use_graph_rag = use_graph_rag
        self.name = 'rag_retrieve_graph_evidence' if use_graph_rag else 'rag_retrieve_evidence'

    def run(self, payload: RagRetrieveEvidenceInput, context) -> EvidencePack:
        """执行证据检索并返回结构化证据包。"""

        return self._rag(context).retrieve_evidence(
            KnowledgeSearchRequest(
                query=payload.query,
                collection_name=payload.collection_name,
                doc_ids=list(payload.doc_ids),
                top_k=payload.top_k,
                focus_aspects=list(payload.focus_aspects),
                use_graph_rag=self.use_graph_rag,
            ),
            trace_context=self._trace_context(context, tool_name=self.name),
        )


class RagGroundedAnswerTool(_BaseRagTool):
    """生成 grounded answer 的工具封装。"""

    name = 'rag_grounded_answer'
    timeout_ms = 20000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status', 'error_type']
    input_model = RagGroundedAnswerInput
    output_model = GroundedAnswerResult

    def run(self, payload: RagGroundedAnswerInput, context) -> GroundedAnswerResult:
        """基于检索与策略配置生成有依据的答案。"""

        return self._rag(context).grounded_answer(
            GroundedAnswerRequest(
                question=payload.question,
                retrieval_query=payload.retrieval_query,
                collection_name=payload.collection_name,
                doc_ids=list(payload.doc_ids),
                top_k=payload.top_k,
                focus_aspects=list(payload.focus_aspects),
                strategy=GroundedAnswerStrategy(
                    use_corrective_rag=payload.use_corrective_rag,
                    use_graph_rag=payload.use_graph_rag,
                ),
            ),
            trace_context=self._trace_context(context, tool_name=self.name),
        )


class RagGroundedQueryTool(_BaseRagTool):
    """执行完整 grounded query 的工具封装。"""

    name = 'rag_grounded_query'
    timeout_ms = 20000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status', 'error_type']
    input_model = RagGroundedQueryInput
    output_model = QueryResponse

    def run(self, payload: RagGroundedQueryInput, context) -> QueryResponse:
        """把查询输入转换为 ``QueryRequest`` 后交给 facade 执行。"""

        request = QueryRequest(
            question=payload.question,
            collection_name=payload.collection_name,
            top_k=payload.top_k,
            session_id=payload.session_id,
            use_graph_rag=payload.use_graph_rag,
            use_corrective_rag=payload.use_corrective_rag,
        )
        return self._rag(context).grounded_query(
            request,
            retrieval_query=payload.retrieval_query,
            trace_context=self._trace_context(context, tool_name=self.name),
        )

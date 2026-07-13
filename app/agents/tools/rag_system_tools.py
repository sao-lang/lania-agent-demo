"""RAG 系统工具适配器。

这组工具通过 ``ToolContext.services['rag_system']`` 调用独立的 ``RagContainer``，
不依赖主应用的 ``RagFacade``／``KnowledgeCapability``，实现 RAG 系统解耦。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import AgentTool, ToolContext, ToolExecutionError, ToolRetryPolicy
from app.rag_system.container import RagContainer
from app.rag_system.models.query import QueryRequest, QueryResponse
from app.rag_system.knowledge.base import KnowledgeSearchRequest


class RagSystemRetrieveInput(BaseModel):
    query: str = Field(description="检索查询文本")
    collection_name: str = Field(description="知识库名称")
    top_k: int = Field(default=6, ge=1, le=20, description="返回结果数量")
    use_hybrid_retrieval: bool = Field(default=False, description="是否启用混合检索（向量+词法）")
    use_rerank: bool = Field(default=True, description="是否对结果重排")
    use_graph_rag: bool = Field(default=False, description="是否启用图谱增强检索")


class RagSystemQueryInput(BaseModel):
    question: str = Field(description="用户问题")
    collection_name: str = Field(description="知识库名称")
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")
    session_id: str | None = Field(default=None, description="会话 ID（多轮对话用）")
    use_graph_rag: bool = Field(default=False, description="是否启用图谱增强检索")
    use_hybrid_retrieval: bool = Field(default=False, description="是否启用混合检索")
    use_corrective_rag: bool = Field(default=False, description="是否启用 Self-RAG 纠偏")
    use_query_rewrite: bool = Field(default=True, description="是否启用查询改写")


class RagSystemIngestInput(BaseModel):
    collection_name: str = Field(description="目标知识库名称")
    file_path: str = Field(description="文件路径")
    doc_id: str | None = Field(default=None, description="可选文档 ID")


class _BaseRagSystemTool:
    """RAG 系统工具基类。"""

    version = 'v1'
    risk_level = 'low'

    def _get_rag_system(self, context: ToolContext) -> RagContainer:
        """从工具上下文中获取 RAG 容器实例。"""
        if context.services and 'rag_system' in context.services:
            return context.services['rag_system']
        raise ToolExecutionError(
            code='rag_system_unavailable',
            message='rag_system not available in context.services',
            error_type='dependency_error',
            default_action='fallback',
        )


class RagSystemRetrieveTool(_BaseRagSystemTool):
    """通过独立 RAG 系统执行证据检索。"""

    name = 'rag_system_retrieve'
    description = '通过独立 RAG 引擎检索证据'
    timeout_ms = 15000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    input_model = RagSystemRetrieveInput
    output_model = dict

    def run(self, payload: RagSystemRetrieveInput, context: ToolContext) -> dict:
        rag = self._get_rag_system(context)
        citations = rag.retrieval.retrieve(
            collection_name=payload.collection_name,
            question=payload.query,
            top_k=payload.top_k,
            use_hybrid_retrieval=payload.use_hybrid_retrieval,
            use_rerank=payload.use_rerank,
            use_graph_rag=payload.use_graph_rag,
        )
        return {
            'citations': [
                {
                    'chunk_id': c.chunk_id,
                    'text': c.text,
                    'source': c.source,
                    'score': c.score,
                }
                for c in citations
            ],
            'count': len(citations),
        }


class RagSystemQueryTool(_BaseRagSystemTool):
    """通过独立 RAG 系统执行完整检索问答。"""

    name = 'rag_system_query'
    description = '通过独立 RAG 引擎执行检索问答'
    timeout_ms = 20000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    input_model = RagSystemQueryInput
    output_model = dict

    def run(self, payload: RagSystemQueryInput, context: ToolContext) -> dict:
        rag = self._get_rag_system(context)
        response = rag.engine.query(QueryRequest(
            question=payload.question,
            collection_name=payload.collection_name,
            top_k=payload.top_k,
            session_id=payload.session_id,
            use_graph_rag=payload.use_graph_rag,
            use_hybrid_retrieval=payload.use_hybrid_retrieval,
            use_corrective_rag=payload.use_corrective_rag,
            use_query_rewrite=payload.use_query_rewrite,
        ))
        return {
            'answer': response.answer,
            'citations': [
                {
                    'chunk_id': c.chunk_id,
                    'text': c.text[:200],
                    'source': c.source,
                    'score': c.score,
                }
                for c in response.citations
            ],
            'retrieved_count': response.retrieved_count,
            'latency_ms': response.latency_ms,
        }


class RagSystemIngestTool(_BaseRagSystemTool):
    """通过独立 RAG 系统导入文档。"""

    name = 'rag_system_ingest'
    description = '通过独立 RAG 引擎导入文档'
    timeout_ms = 60000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=500)
    input_model = RagSystemIngestInput
    output_model = dict

    def run(self, payload: RagSystemIngestInput, context: ToolContext) -> dict:
        rag = self._get_rag_system(context)
        result = rag.ingestion.ingest_file(
            collection_name=payload.collection_name,
            file_path=payload.file_path,
            doc_id=payload.doc_id,
        )
        return {
            'doc_id': result.get('doc_id'),
            'file_name': result.get('file_name'),
            'chunks': result.get('chunks', 0),
            'collection_name': result.get('collection_name'),
            'graph': result.get('graph'),
        }

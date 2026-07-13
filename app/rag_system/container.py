"""RAG 系统依赖容器模块。

负责把配置、存储、检索、摄取、查询等组件按依赖顺序装配成一个独立容器，
供 API 层、主应用和独立部署共享。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.rag_system.config.settings import RagSettings
from app.rag_system.knowledge.service import RagKnowledgeCapability
from app.rag_system.ingestion.service import RagIngestionService
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.query.engine import RagQueryEngine, SSEEvent
from app.rag_system.query.facade import RagFacade
from app.rag_system.retrieval.graph_service import RagGraphService
from app.rag_system.retrieval.service import RagRetrievalService
from app.rag_system.answer.semantic_cache import SemanticCacheService
from app.rag_system.store.persistence import RagPersistence
from app.rag_system.store.state import RagState
from app.rag_system.vector_store.chroma import ChromaClientFactory
from app.rag_system.vector_store.llamaindex_adapter import build_llm, build_embed_model


class RagContainer:
    """RAG 系统独立容器。

    管理 RAG 组件生命周期，可独立启动 API 服务或作为主应用的依赖包使用。
    """

    def __init__(self, settings: RagSettings | None = None) -> None:
        """初始化 RAG 容器并装配所有组件。

        Args:
            settings: RAG 系统配置；为 None 时从环境变量自动加载。
        """
        self.settings = settings or RagSettings()

        # ── 基础设施 ──
        self.state = RagState()
        self.persistence = RagPersistence(self.settings)
        self.trace = TraceRecorder()
        self.vector_store = ChromaClientFactory(self.settings)
        self.llm = build_llm(self.settings)
        self.embed_model = build_embed_model(self.settings)

        # 从持久化恢复状态
        self.persistence.load_into(self.state)

        # ── 图谱服务 ──
        self.graph_service = RagGraphService(
            state=self.state,
            vector_store=self.vector_store,
            trace=self.trace,
            persistence=self.persistence,
            llm=self.llm,
        )

        # ── 检索服务 ──
        self.retrieval = RagRetrievalService(
            settings=self.settings,
            state=self.state,
            vector_store=self.vector_store,
            trace=self.trace,
            graph_service=self.graph_service,
        )

        # ── 语义缓存 ──
        self.semantic_cache = SemanticCacheService(
            settings=self.settings,
            state=self.state,
            embed_model=self.embed_model,
            trace=self.trace,
            persistence=self.persistence,
        )

        # ── 文档摄取 ──
        self.ingestion = RagIngestionService(
            settings=self.settings,
            state=self.state,
            vector_store=self.vector_store,
            trace=self.trace,
            persistence=self.persistence,
            graph_service=self.graph_service,
        )

        # ── 知识能力 ──
        self.knowledge_capability = RagKnowledgeCapability(
            state=self.state,
            retrieval=self.retrieval,
            vector_store=self.vector_store,
            llm=self.llm,
        )

        # ── 门面 ──
        self.facade = RagFacade(self.knowledge_capability)

        # ── 查询引擎 ──
        self.engine = RagQueryEngine(
            settings=self.settings,
            state=self.state,
            retrieval_service=self.retrieval,
            trace=self.trace,
            persistence=self.persistence,
            semantic_cache=self.semantic_cache,
            knowledge_capability=self.knowledge_capability,
        )

    @property
    def api_router(self):
        """获取 FastAPI 路由，供独立部署或主应用挂载使用。

        注意：此路由本身不含前缀，由挂载方指定。
        """
        from fastapi import APIRouter
        from app.rag_system.api.health import router as health_router
        from app.rag_system.api.query import router as query_router
        from app.rag_system.api.documents import router as documents_router
        from app.rag_system.api.collections import router as collections_router

        router = APIRouter()
        router.include_router(health_router, tags=['rag-health'])
        router.include_router(query_router, tags=['rag-query'])
        router.include_router(documents_router, prefix='/documents', tags=['rag-documents'])
        router.include_router(collections_router, prefix='/collections', tags=['rag-collections'])
        return router

    # ── LangGraph 图编排 ─────────────────────────────────────

    @property
    def graph_engine(self):
        """懒加载的 LangGraph 查询工作流图。

        如需自定义 TaskSpec 步骤顺序，请直接使用 ``RagQueryGraphBuilder``。
        """
        if not hasattr(self, '_graph'):
            from app.rag_system.query.graph import RagQueryEngineAdapter, RagQueryGraphBuilder
            adapter = RagQueryEngineAdapter(self.engine)
            builder = RagQueryGraphBuilder(adapter, trace=self.trace)
            self._graph = builder.build()
        return self._graph

    def graph_query(
        self,
        request: Any,
        steps: list[dict[str, str]] | None = None,
    ) -> Any:
        """使用 LangGraph 工作流执行单轮检索问答。

        Args:
            request: QueryRequest 或 ChatRequest 对象。
            steps: 可选的自定义步骤顺序；为 None 时使用默认顺序。

        Returns:
            QueryResponse 响应。
        """
        from app.rag_system.query.graph import init_query_graph_state
        from app.rag_system.models.query import QueryRequest, ChatRequest

        default_steps = steps or [
            {'step_id': 'check_guardrails', 'name': '护栏检查'},
            {'step_id': 'rewrite_query', 'name': '改写'},
            {'step_id': 'expand_queries', 'name': '多路展开'},
            {'step_id': 'lookup_cache', 'name': '查询缓存'},
            {'step_id': 'retrieve_evidence', 'name': '检索证据'},
            {'step_id': 'compress_context', 'name': '压缩上下文'},
            {'step_id': 'grounded_answer', 'name': '生成回答'},
            {'step_id': 'self_reflect', 'name': '自检反思'},
            {'step_id': 'persist_session', 'name': '持久化'},
            {'step_id': 'finalize', 'name': '完成'},
        ]
        is_chat = isinstance(request, ChatRequest)
        mode = 'chat' if is_chat else 'query'
        state = init_query_graph_state(
            mode=mode,
            request=request,
            task_spec={'task_id': f'graph-{id(request)}', 'steps': default_steps},
            task_run={
                'task_run_id': f'run-{id(request)}',
                'task_id': f'graph-{id(request)}',
                'status': 'running',
                'current_step_id': None, 'step_attempts': {},
                'step_runtimes': {}, 'run_events': [],
            },
        )
        result = self.graph_engine.invoke(state)
        return result.get('result')

    def graph_stream(
        self,
        request: Any,
        steps: list[dict[str, str]] | None = None,
    ) -> Iterator[SSEEvent]:
        """使用 LangGraph 工作流执行流式检索问答，逐步骤输出 SSE 事件。

        Args:
            request: QueryRequest 请求。
            steps: 可选的自定义步骤顺序。

        Yields:
            SSE 事件字典。
        """
        from app.rag_system.query.graph import init_query_graph_state
        from app.rag_system.models.query import QueryRequest

        default_steps = steps or [
            {'step_id': 'check_guardrails', 'name': '护栏检查'},
            {'step_id': 'rewrite_query', 'name': '改写'},
            {'step_id': 'expand_queries', 'name': '多路展开'},
            {'step_id': 'lookup_cache', 'name': '查询缓存'},
            {'step_id': 'retrieve_evidence', 'name': '检索证据'},
            {'step_id': 'compress_context', 'name': '压缩上下文'},
            {'step_id': 'grounded_answer', 'name': '生成回答'},
            {'step_id': 'self_reflect', 'name': '自检反思'},
            {'step_id': 'persist_session', 'name': '持久化'},
            {'step_id': 'finalize', 'name': '完成'},
        ]
        state = init_query_graph_state(
            mode='query_stream',
            request=request,
            task_spec={'task_id': f'graph-s-{id(request)}', 'steps': default_steps},
            task_run={
                'task_run_id': f'run-s-{id(request)}',
                'task_id': f'graph-s-{id(request)}',
                'status': 'running',
                'current_step_id': None, 'step_attempts': {},
                'step_runtimes': {}, 'run_events': [],
            },
        )
        # LangGraph stream() 逐节点输出状态更新 → 从中提取 events
        for chunk in self.graph_engine.stream(state):
            for node_name, node_state in chunk.items():
                events = node_state.get('events') or node_state.get('event') or []
                if isinstance(events, list):
                    yield from events
                elif isinstance(events, dict):
                    yield events

    def start(self, host: str = '0.0.0.0', port: int = 8001) -> None:
        """启动独立 API 服务（微服务模式）。"""
        import uvicorn
        from fastapi import FastAPI

        app = FastAPI(
            title='RAG Service',
            version='0.1.0',
            description='独立 RAG 检索服务',
        )
        app.state.rag_container = self
        app.include_router(self.api_router)
        uvicorn.run(app, host=host, port=port)

    def shutdown(self) -> None:
        """释放容器资源。"""
        # 清理资源（如需要）
        pass

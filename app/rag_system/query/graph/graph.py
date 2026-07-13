"""RAG 系统查询工作流图构建模块。

负责把 ``RagQueryGraphNodes`` 组织成一个最小可运行的 LangGraph 状态图，
承载带 Corrective RAG 能力的查询工作流。

与 ``app/workflows/query_graph.py`` 功能一致，但不依赖主应用的 harness 基础设施。
"""

from __future__ import annotations

from typing import Any

from app.rag_system.query.graph.nodes import RagQueryGraphNodes
from app.rag_system.query.graph.runtime import RagGraphRuntime
from app.rag_system.query.graph.state import QUERY_GRAPH_ENTRY_ROUTES, QueryGraphState


class QueryGraphNodeExecutionError(RuntimeError):
    """包装 graph node 执行异常，并携带局部 state。"""

    def __init__(self, message: str, partial_state: Any) -> None:
        super().__init__(message)
        self.partial_state = partial_state


def _wrap_query_node(nodes: RagQueryGraphNodes, node_name: str, handler: Any) -> Any:
    """为 query graph 节点补统一异常包装。"""
    def _wrapped(state: Any) -> Any:
        try:
            update = handler(state)
            nodes.validate_node_contract(node_name, update)
            return update
        except Exception as exc:
            nodes.handle_node_exception(node_name, state, exc)
            raise QueryGraphNodeExecutionError(str(exc), state) from exc
    return _wrapped


def _resolve_query_entry_route(state: Any) -> str:
    """解析 query graph 入口路由，支持 replay/resume 场景。"""
    route = state.get('graph_entry_route') or 'check_guardrails'
    if route not in QUERY_GRAPH_ENTRY_ROUTES:
        raise RuntimeError(f'unsupported query graph entry route: {route}')
    return route


class RagQueryGraphBuilder:
    """RAG 查询工作流图构建器。

    将 RAG 查询链路拆分为 16 个 LangGraph 节点，通过条件路由连接。
    """

    DEFAULT_ENTRY_ROUTE = 'check_guardrails'

    def __init__(
        self,
        runtime: RagGraphRuntime,
        trace: Any | None = None,
        knowledge_capability: Any | None = None,
    ) -> None:
        """初始化图构建器。

        Args:
            runtime: 符合 ``RagGraphRuntime`` 协议的 RAG 引擎。
            trace: 可选的追踪记录器。
            knowledge_capability: 可选的知识能力实例。
        """
        self.runtime = runtime
        self.trace = trace
        self.knowledge_capability = knowledge_capability

    def build(self) -> Any:
        """构建并编译 LangGraph StateGraph。

        Returns:
            编译完成的查询工作流图对象（compiled StateGraph）。

        Raises:
            RuntimeError: 当前环境未安装 langgraph 依赖时抛出。
        """
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError(
                'RAG graph workflow 依赖 langgraph，请先安装对应依赖。'
            ) from exc

        nodes = RagQueryGraphNodes(
            runtime=self.runtime,
            trace=self.trace,
            knowledge_capability=self.knowledge_capability,
        )
        graph = StateGraph(QueryGraphState)

        # ── 虚拟入口节点 ──
        graph.add_node('__entry__', lambda _state: {})

        # ── 步骤节点 ──
        graph.add_node(
            'check_guardrails',
            _wrap_query_node(nodes, 'check_guardrails', nodes.check_guardrails),
        )
        graph.add_node(
            'dispatch_query_step',
            _wrap_query_node(nodes, 'dispatch_query_step', nodes.dispatch_query_step),
        )
        graph.add_node(
            'blocked_response',
            _wrap_query_node(nodes, 'blocked_response', nodes.blocked_response),
        )
        graph.add_node(
            'load_session_context',
            _wrap_query_node(nodes, 'load_session_context', nodes.load_session_context),
        )
        graph.add_node(
            'rewrite_query',
            _wrap_query_node(nodes, 'rewrite_query', nodes.rewrite_query),
        )
        graph.add_node(
            'expand_queries',
            _wrap_query_node(nodes, 'expand_queries', nodes.expand_queries),
        )
        graph.add_node(
            'lookup_cache',
            _wrap_query_node(nodes, 'lookup_cache', nodes.lookup_cache),
        )
        graph.add_node(
            'cache_hit_response',
            _wrap_query_node(nodes, 'cache_hit_response', nodes.cache_hit_response),
        )
        graph.add_node(
            'retrieve_evidence',
            _wrap_query_node(nodes, 'retrieve_evidence', nodes.retrieve_evidence),
        )
        graph.add_node(
            'compress_context',
            _wrap_query_node(nodes, 'compress_context', nodes.compress_context),
        )
        graph.add_node(
            'grounded_answer',
            _wrap_query_node(nodes, 'grounded_answer', nodes.grounded_answer),
        )
        graph.add_node(
            'self_reflect',
            _wrap_query_node(nodes, 'self_reflect', nodes.self_reflect),
        )
        graph.add_node(
            'retry_retrieve',
            _wrap_query_node(nodes, 'retry_retrieve', nodes.retry_retrieve),
        )
        graph.add_node(
            'rewrite_answer',
            _wrap_query_node(nodes, 'rewrite_answer', nodes.rewrite_answer),
        )
        graph.add_node(
            'persist_session',
            _wrap_query_node(nodes, 'persist_session', nodes.persist_session),
        )
        graph.add_node(
            'finalize',
            _wrap_query_node(nodes, 'finalize', nodes.finalize),
        )

        # ── 边：入口路由 ──
        graph.add_edge(START, '__entry__')
        graph.add_conditional_edges(
            '__entry__',
            _resolve_query_entry_route,
            {route: route for route in QUERY_GRAPH_ENTRY_ROUTES},
        )

        # ── 边：护栏 → 响应/分发 ──
        graph.add_conditional_edges(
            'check_guardrails',
            nodes.route_guardrail,
            {
                'blocked_response': 'blocked_response',
                'dispatch_query_step': 'dispatch_query_step',
            },
        )

        # ── 边：分发器 → 各步骤 ──
        graph.add_conditional_edges(
            'dispatch_query_step',
            nodes.route_query_step,
            {
                'load_session_context': 'load_session_context',
                'rewrite_query': 'rewrite_query',
                'expand_queries': 'expand_queries',
                'lookup_cache': 'lookup_cache',
                'retrieve_evidence': 'retrieve_evidence',
                'compress_context': 'compress_context',
                'grounded_answer': 'grounded_answer',
                'self_reflect': 'self_reflect',
                'persist_session': 'persist_session',
                'finalize': 'finalize',
            },
        )

        # ── 边：拦截响应 → 持久化/结束 ──
        graph.add_conditional_edges(
            'blocked_response',
            lambda state: nodes.route_orchestration(state, from_step='blocked_response'),
            {'persist_session': 'persist_session', 'finalize': 'finalize'},
        )

        # ── 边：顺序步骤（执行后回到分发器） ──
        graph.add_edge('load_session_context', 'dispatch_query_step')
        graph.add_edge('rewrite_query', 'dispatch_query_step')
        graph.add_edge('expand_queries', 'dispatch_query_step')

        # ── 边：缓存 ──
        graph.add_conditional_edges(
            'lookup_cache',
            nodes.route_cache,
            {
                'cache_hit_response': 'cache_hit_response',
                'dispatch_query_step': 'dispatch_query_step',
                'finalize': 'finalize',
            },
        )

        # ── 边：缓存命中响应 → 持久化/结束 ──
        graph.add_conditional_edges(
            'cache_hit_response',
            lambda state: nodes.route_orchestration(state, from_step='cache_hit_response'),
            {'persist_session': 'persist_session', 'finalize': 'finalize'},
        )

        # ── 边：检索/压缩/回答 → 回到分发器 ──
        graph.add_edge('retrieve_evidence', 'dispatch_query_step')
        graph.add_edge('compress_context', 'dispatch_query_step')
        graph.add_edge('grounded_answer', 'dispatch_query_step')

        # ── 边：Self-RAG 反思 ──
        graph.add_conditional_edges(
            'self_reflect',
            nodes.route_reflection,
            {
                'retry_retrieve': 'retry_retrieve',
                'rewrite_answer': 'rewrite_answer',
                'dispatch_query_step': 'dispatch_query_step',
            },
        )

        # ── 边：重试检索 → 回到检索 ──
        graph.add_conditional_edges(
            'retry_retrieve',
            lambda state: nodes.route_orchestration(state, from_step='retry_retrieve'),
            {'retrieve_evidence': 'retrieve_evidence'},
        )

        # ── 边：改写答案 → 回到分发器 ──
        graph.add_conditional_edges(
            'rewrite_answer',
            lambda state: nodes.route_orchestration(state, from_step='rewrite_answer'),
            {'dispatch_query_step': 'dispatch_query_step'},
        )

        # ── 边：结束 ──
        graph.add_edge('persist_session', 'finalize')
        graph.add_edge('finalize', END)

        return graph.compile()

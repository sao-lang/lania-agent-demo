"""查询工作流图构建模块。

负责把 `QueryWorkflowNodes` 组织成一个最小可运行的 LangGraph 状态图，当前主要承载
带 Corrective RAG 能力的查询工作流。
"""

from __future__ import annotations

from typing import Any

from app.harness.context import ContextHarness
from app.harness.execution import ExecutionHarness
from app.harness.reflection import ReflectionHarness
from app.harness.react_runtime import BoundedLocalReActRuntime
from app.rag.query_engine import RagQueryEngine
from app.rag.observability import TraceRecorder
from app.workflows.query_nodes import QueryWorkflowNodes
from app.workflows.query_runtime import ensure_query_workflow_runtime

DEFAULT_QUERY_ENTRY_ROUTE = 'check_guardrails'
QUERY_GRAPH_ENTRY_ROUTES = (
    'check_guardrails',
    'dispatch_query_step',
    'blocked_response',
    'load_session_context',
    'rewrite_query',
    'expand_queries',
    'lookup_cache',
    'cache_hit_response',
    'retrieve_evidence',
    'compress_context',
    'grounded_answer',
    'self_reflect',
    'retry_retrieve',
    'rewrite_answer',
    'persist_session',
    'finalize',
)


class QueryGraphNodeExecutionError(RuntimeError):
    """包装 graph node 执行异常，并携带局部 state。"""

    def __init__(self, message: str, partial_state: Any) -> None:
        super().__init__(message)
        self.partial_state = partial_state


def _wrap_query_node(nodes: QueryWorkflowNodes, node_name: str, handler: Any) -> Any:
    """为 query graph 节点补统一异常包装。

    包装后的节点会在异常发生时先调用 `nodes.handle_node_exception` 写回局部运行态，再把异常
    包装成携带 `partial_state` 的 `QueryGraphNodeExecutionError`，方便 orchestrator 在失败时
    继续输出部分事件或恢复上下文。
    """

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
    """解析 query graph 入口路由。

    该入口主要用于 replay/resume 场景，让工作流能够从 checkpoint 指定的节点重新进入。
    """
    route = state.get('graph_entry_route') or DEFAULT_QUERY_ENTRY_ROUTE
    if route not in QUERY_GRAPH_ENTRY_ROUTES:
        raise RuntimeError(f'unsupported query graph entry route: {route}')
    return route


def build_query_graph(
    classic_engine: RagQueryEngine,
    trace: TraceRecorder,
    capabilities: dict[str, Any] | None = None,
    context_harness: ContextHarness | None = None,
    execution_harness: ExecutionHarness | None = None,
    react_runtime: BoundedLocalReActRuntime | None = None,
    reflection_harness: ReflectionHarness | None = None,
) -> Any:
    """构建最小可运行的 StateGraph，优先承载 Corrective RAG。

    Args:
        classic_engine: 经典查询引擎实现，供节点复用底层能力。
        trace: 链路追踪记录器。

    Returns:
        编译完成的查询工作流图对象。

    Raises:
        RuntimeError: 当当前环境未安装 LangGraph 依赖时抛出。
    """

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - 仅在缺少依赖时触发
        raise RuntimeError('query workflow 依赖 langgraph，请先安装对应依赖。') from exc

    from app.workflows.query_state import QueryGraphState

    nodes = QueryWorkflowNodes(
        ensure_query_workflow_runtime(classic_engine),
        trace,
        capabilities=capabilities,
        context_harness=context_harness,
        execution_harness=execution_harness,
        react_runtime=react_runtime,
        reflection_harness=reflection_harness,
    )
    graph = StateGraph(QueryGraphState)
    # 通过虚拟入口节点统一接收首次执行、回放与恢复三类入口，再按路由分发到真实节点。
    graph.add_node('__entry__', lambda _state: {})
    # 节点顺序由 TaskSpec.steps 驱动，graph 只负责声明可执行节点与分支跳转。
    graph.add_node('check_guardrails', _wrap_query_node(nodes, 'check_guardrails', nodes.check_guardrails))
    graph.add_node('dispatch_query_step', _wrap_query_node(nodes, 'dispatch_query_step', nodes.dispatch_query_step))
    graph.add_node('blocked_response', _wrap_query_node(nodes, 'blocked_response', nodes.blocked_response))
    graph.add_node('load_session_context', _wrap_query_node(nodes, 'load_session_context', nodes.load_session_context))
    graph.add_node('rewrite_query', _wrap_query_node(nodes, 'rewrite_query', nodes.rewrite_query))
    graph.add_node('expand_queries', _wrap_query_node(nodes, 'expand_queries', nodes.expand_queries))
    graph.add_node('lookup_cache', _wrap_query_node(nodes, 'lookup_cache', nodes.lookup_cache))
    graph.add_node('cache_hit_response', _wrap_query_node(nodes, 'cache_hit_response', nodes.cache_hit_response))
    graph.add_node('retrieve_evidence', _wrap_query_node(nodes, 'retrieve_evidence', nodes.retrieve_evidence))
    graph.add_node('compress_context', _wrap_query_node(nodes, 'compress_context', nodes.compress_context))
    graph.add_node('grounded_answer', _wrap_query_node(nodes, 'grounded_answer', nodes.grounded_answer))
    graph.add_node('self_reflect', _wrap_query_node(nodes, 'self_reflect', nodes.self_reflect))
    graph.add_node('retry_retrieve', _wrap_query_node(nodes, 'retry_retrieve', nodes.retry_retrieve))
    graph.add_node('rewrite_answer', _wrap_query_node(nodes, 'rewrite_answer', nodes.rewrite_answer))
    graph.add_node('persist_session', _wrap_query_node(nodes, 'persist_session', nodes.persist_session))
    graph.add_node('finalize', _wrap_query_node(nodes, 'finalize', nodes.finalize))
    graph.add_edge(START, '__entry__')
    graph.add_conditional_edges(
        '__entry__',
        _resolve_query_entry_route,
        {route: route for route in QUERY_GRAPH_ENTRY_ROUTES},
    )
    graph.add_conditional_edges(
        'check_guardrails',
        nodes.route_guardrail,
        {
            'blocked_response': 'blocked_response',
            'dispatch_query_step': 'dispatch_query_step',
        },
    )
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
    graph.add_conditional_edges(
        'blocked_response',
        lambda state: nodes.route_orchestration(state, from_step='blocked_response'),
        {
            'persist_session': 'persist_session',
            'finalize': 'finalize',
        },
    )
    graph.add_edge('load_session_context', 'dispatch_query_step')
    graph.add_edge('rewrite_query', 'dispatch_query_step')
    graph.add_edge('expand_queries', 'dispatch_query_step')
    graph.add_conditional_edges(
        'lookup_cache',
        nodes.route_cache,
        {
            'cache_hit_response': 'cache_hit_response',
            'dispatch_query_step': 'dispatch_query_step',
            'persist_session': 'persist_session',
            'finalize': 'finalize',
        },
    )
    graph.add_conditional_edges(
        'cache_hit_response',
        lambda state: nodes.route_orchestration(state, from_step='cache_hit_response'),
        {
            'persist_session': 'persist_session',
            'finalize': 'finalize',
        },
    )
    graph.add_edge('retrieve_evidence', 'dispatch_query_step')
    graph.add_edge('compress_context', 'dispatch_query_step')
    graph.add_edge('grounded_answer', 'dispatch_query_step')
    graph.add_conditional_edges(
        'self_reflect',
        nodes.route_reflection,
        {
            'retry_retrieve': 'retry_retrieve',
            'rewrite_answer': 'rewrite_answer',
            'dispatch_query_step': 'dispatch_query_step',
        },
    )
    graph.add_conditional_edges(
        'retry_retrieve',
        lambda state: nodes.route_orchestration(state, from_step='retry_retrieve'),
        {'retrieve_evidence': 'retrieve_evidence'},
    )
    graph.add_conditional_edges(
        'rewrite_answer',
        lambda state: nodes.route_orchestration(state, from_step='rewrite_answer'),
        {'dispatch_query_step': 'dispatch_query_step'},
    )
    graph.add_edge('persist_session', 'finalize')
    graph.add_edge('finalize', END)
    return graph.compile()

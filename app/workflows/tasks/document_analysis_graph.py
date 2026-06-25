"""文档分析任务图构建模块。

负责把 `DocumentAnalysisNodes` 组织成可执行的 LangGraph 状态图，并明确计划分发、补证据、
审查修订和最终交付之间的路由关系。该模块属于任务编排层，不直接承载具体分析逻辑。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from app.agents.memory import TaskMemory
from app.agents.planner import TaskPlanner
from app.agents.subagents import SubAgentRuntime
from app.agents.tools.registry import ToolRegistry
from app.core.config import Settings
from app.harness.context import ContextHarness
from app.harness.evaluation import EvaluationHarness
from app.harness.execution import ExecutionHarness
from app.harness.guardrails import GuardrailEngine
from app.harness.policy import PolicyEngine
from app.rag.observability import TraceRecorder
from app.services.state import InMemoryState
from app.workflows.tasks.document_analysis_nodes import DocumentAnalysisNodes

DEFAULT_TASK_ENTRY_ROUTE = 'load_task'
TASK_GRAPH_ENTRY_ROUTES = (
    'load_task',
    'plan_task',
    'dispatch_plan_step',
    'collect_document_context',
    'retrieve_evidence',
    'handle_evidence_gap',
    'analyze',
    'draft_artifact',
    'review_artifact',
    'revise_artifact',
    'evaluate_exit_criteria',
    'finalize',
)


def _resolve_task_entry_route(state: dict[str, Any]) -> str:
    """解析任务图入口路由。

    该入口主要用于 checkpoint replay / resume 场景，使任务运行时可以从指定步骤继续执行。
    """
    route = cast(str | None, state.get('graph_entry_route')) or DEFAULT_TASK_ENTRY_ROUTE
    if route not in TASK_GRAPH_ENTRY_ROUTES:
        raise RuntimeError(f'unsupported task graph entry route: {route}')
    return route


def _wrap_task_node(
    node_name: str,
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    checkpoint_step_id: str | None = None,
    next_route: str | None = None,
    checkpoint_after_step: Callable[[dict[str, Any], str, str], dict[str, Any]] | None = None,
    on_node_error: Callable[[dict[str, Any], str, Exception], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """包装任务节点，补充统一 checkpoint 与异常处理钩子。

    包装后的节点可以在成功后按需创建 checkpoint，并在失败时把异常上报给编排器层，以便统一
    写回运行态、持久化中间状态或生成恢复信息。
    """
    def _wrapped(state: dict[str, Any]) -> dict[str, Any]:
        try:
            update = cast(dict[str, Any], handler(state))
            if checkpoint_step_id is not None and next_route is not None and checkpoint_after_step is not None:
                merged_state = cast(dict[str, Any], {**state, **update})
                update = {
                    **update,
                    **checkpoint_after_step(merged_state, checkpoint_step_id, next_route),
                }
            return update
        except Exception as exc:
            if on_node_error is not None:
                on_node_error(state, node_name, exc)
            raise

    return _wrapped


def build_document_analysis_graph(
    planner: TaskPlanner,
    registry: ToolRegistry,
    memory: TaskMemory,
    trace: TraceRecorder,
    settings: Settings,
    state: InMemoryState,
    retrieval,
    vector_store,
    llm,
    subagent_runtime: SubAgentRuntime,
    context_harness: ContextHarness,
    execution_harness: ExecutionHarness,
    guardrail_engine: GuardrailEngine,
    policy_engine: PolicyEngine,
    evaluation_harness: EvaluationHarness,
    checkpoint_after_step: Callable[[dict[str, Any], str, str], dict[str, Any]] | None = None,
    on_node_error: Callable[[dict[str, Any], str, Exception], None] | None = None,
) -> Any:
    """构建用于 document analysis 的 LangGraph。

    Args:
        planner: 任务计划生成器。
        registry: 工具注册表。
        memory: 任务记忆服务。
        trace: 链路追踪记录器。
        settings: 全局配置对象。
        state: 内存态业务数据。
        retrieval: 检索服务实例。
        vector_store: 向量库访问封装。
        llm: 可选大模型实例。
        subagent_runtime: 子 Agent 运行时。

    Returns:
        编译完成的文档分析工作流图对象。

    Raises:
        RuntimeError: 当当前环境未安装 `langgraph` 依赖时抛出。
    """

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Document Analysis workflow 需要安装 langgraph 依赖。') from exc

    from app.workflows.tasks.document_analysis_state import DocumentAnalysisState

    nodes = DocumentAnalysisNodes(
        planner,
        registry,
        memory,
        trace,
        settings,
        state,
        retrieval,
        vector_store,
        llm,
        subagent_runtime,
        context_harness,
        execution_harness,
        guardrail_engine,
        policy_engine,
        evaluation_harness,
    )
    graph = StateGraph(DocumentAnalysisState)
    # 虚拟入口节点统一承接首次执行、重放与恢复，再根据 graph_entry_route 分发到真实步骤。
    graph.add_node('__entry__', lambda _state: {})
    # 主链路按“载入任务 -> 生成计划 -> 分发步骤 -> 审查/修订 -> 退出判断 -> 最终交付”组织。
    graph.add_node('load_task', cast(Any, _wrap_task_node('load_task', nodes.load_task, on_node_error=on_node_error)))
    graph.add_node('plan_task', cast(Any, _wrap_task_node('plan_task', nodes.plan_task, on_node_error=on_node_error)))
    graph.add_node(
        'dispatch_plan_step',
        cast(Any, _wrap_task_node('dispatch_plan_step', nodes.dispatch_plan_step, on_node_error=on_node_error)),
    )
    graph.add_node(
        'collect_document_context',
        cast(
            Any,
            _wrap_task_node(
                'collect_document_context',
                nodes.collect_document_context,
                checkpoint_step_id='collect_document_context',
                next_route='dispatch_plan_step',
                checkpoint_after_step=checkpoint_after_step,
                on_node_error=on_node_error,
            ),
        ),
    )
    graph.add_node(
        'retrieve_evidence',
        cast(
            Any,
            _wrap_task_node(
                'retrieve_evidence',
                nodes.retrieve_evidence,
                checkpoint_step_id='retrieve_evidence',
                next_route='dispatch_plan_step',
                checkpoint_after_step=checkpoint_after_step,
                on_node_error=on_node_error,
            ),
        ),
    )
    graph.add_node(
        'handle_evidence_gap',
        cast(
            Any,
            _wrap_task_node(
                'handle_evidence_gap',
                nodes.handle_evidence_gap,
                checkpoint_step_id='handle_evidence_gap',
                next_route='dispatch_plan_step',
                checkpoint_after_step=checkpoint_after_step,
                on_node_error=on_node_error,
            ),
        ),
    )
    graph.add_node('analyze', cast(Any, _wrap_task_node('analyze', nodes.analyze, on_node_error=on_node_error)))
    graph.add_node(
        'draft_artifact',
        cast(
            Any,
            _wrap_task_node(
                'draft_artifact',
                nodes.draft_artifact,
                checkpoint_step_id='draft_artifact',
                next_route='review_artifact',
                checkpoint_after_step=checkpoint_after_step,
                on_node_error=on_node_error,
            ),
        ),
    )
    graph.add_node(
        'review_artifact',
        cast(Any, _wrap_task_node('review_artifact', nodes.review_artifact, on_node_error=on_node_error)),
    )
    graph.add_node(
        'revise_artifact',
        cast(Any, _wrap_task_node('revise_artifact', nodes.revise_artifact, on_node_error=on_node_error)),
    )
    graph.add_node(
        'evaluate_exit_criteria',
        cast(
            Any,
            _wrap_task_node('evaluate_exit_criteria', nodes.evaluate_exit_criteria, on_node_error=on_node_error),
        ),
    )
    graph.add_node('finalize', cast(Any, _wrap_task_node('finalize', nodes.finalize, on_node_error=on_node_error)))
    graph.add_edge(START, '__entry__')
    graph.add_conditional_edges(
        '__entry__',
        _resolve_task_entry_route,
        {route: route for route in TASK_GRAPH_ENTRY_ROUTES},
    )
    graph.add_edge('load_task', 'plan_task')
    graph.add_edge('plan_task', 'dispatch_plan_step')
    graph.add_conditional_edges(
        'dispatch_plan_step',
        nodes.route_plan_step,
        {
            'collect_document_context': 'collect_document_context',
            'retrieve_evidence': 'retrieve_evidence',
            'handle_evidence_gap': 'handle_evidence_gap',
            'analyze': 'analyze',
            'draft_artifact': 'draft_artifact',
            'revise_artifact': 'revise_artifact',
            'evaluate_exit_criteria': 'evaluate_exit_criteria',
        },
    )
    graph.add_edge('collect_document_context', 'dispatch_plan_step')
    graph.add_edge('retrieve_evidence', 'dispatch_plan_step')
    graph.add_edge('handle_evidence_gap', 'dispatch_plan_step')
    graph.add_edge('analyze', 'dispatch_plan_step')
    graph.add_edge('draft_artifact', 'review_artifact')
    graph.add_conditional_edges(
        'review_artifact',
        nodes.route_after_review,
        {
            'dispatch_plan_step': 'dispatch_plan_step',
            'evaluate_exit_criteria': 'evaluate_exit_criteria',
        },
    )
    graph.add_edge('revise_artifact', 'review_artifact')
    graph.add_conditional_edges(
        'evaluate_exit_criteria',
        nodes.route_exit_decision,
        {
            'finalize': 'finalize',
            'revise_artifact': 'revise_artifact',
        },
    )
    graph.add_edge('finalize', END)
    return graph.compile()

"""查询工作流状态模块。

负责定义 LangGraph 查询工作流在节点间共享的状态结构和初始化逻辑。该模块处于 workflow
基础层，为查询节点、路由函数和编排器提供统一的状态字段约定。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal, TypedDict

try:
    from typing import NotRequired, Required
except ImportError:  # pragma: no cover - Python < 3.11
    from typing_extensions import NotRequired, Required

from app.harness.models import ContextBundle
from app.models.runtime_contracts import GraphSubgraph, GroundedContext, MemoryRecord, PromptBuildRequest, PromptBuildResult, PromptSpec, ResultContract
from app.models.query import ChatRequest, CitationItem, QueryRequest, QueryResponse
from app.capabilities.knowledge.contracts import RetrievalQualityReport
from app.models.task import CheckpointRecord, ReflectionDecision, StepRuntimeRecord, TaskRun, TaskSpec
from app.types import SSEEvent

# `WorkflowMode` 明确区分“请求语义”与“输出方式”，便于节点根据模式控制会话读写和 SSE 行为。
WorkflowMode = Literal['query', 'chat', 'query_stream', 'chat_stream']
# LangGraph 节点更新统一使用松散字典，具体字段约束由 `QueryGraphState` 和节点契约共同约束。
QueryGraphUpdate = dict[str, Any]


class QueryGraphState(TypedDict, total=False):
    """描述 query workflow 在节点间传递的共享状态。

    该状态同时承载请求对象、SSE 事件、缓存命中信息、检索结果、回答生成中间态，以及
    Self-RAG 重试控制字段，保证各节点能够在不直接耦合彼此实现的情况下共享执行上下文。
    """

    mode: Required[WorkflowMode]
    request: Required[QueryRequest | ChatRequest]
    task_spec: Required[TaskSpec]
    task_run: Required[TaskRun]
    started_at: Required[float]
    result: Required[QueryResponse | None]
    events: Required[list[SSEEvent]]
    run_events: Required[list[dict[str, Any]]]
    metadata: Required[dict[str, Any]]
    error: Required[str | None]
    current_step_id: NotRequired[str | None]
    completed_step_ids: NotRequired[list[str]]
    step_runtimes: NotRequired[dict[str, StepRuntimeRecord]]
    checkpoints: NotRequired[list[CheckpointRecord]]
    context_bundles: NotRequired[dict[str, ContextBundle]]
    memory_records: NotRequired[list[MemoryRecord]]
    prompt_specs: NotRequired[list[PromptSpec]]
    prompt_build_requests: NotRequired[list[PromptBuildRequest]]
    prompt_build_results: NotRequired[list[PromptBuildResult]]
    grounded_context: NotRequired[GroundedContext | None]
    graph_subgraph: NotRequired[GraphSubgraph | None]
    retrieval_quality_report: NotRequired[RetrievalQualityReport | None]
    reflection_decision: NotRequired[ReflectionDecision | None]
    result_contract: NotRequired[ResultContract | None]
    replayed_from_checkpoint_id: NotRequired[str | None]
    graph_entry_route: NotRequired[str | None]
    orchestration_next_route: NotRequired[str | None]
    guardrail_state: NotRequired[dict[str, Any]]
    rewrite_info: NotRequired[dict[str, Any] | None]
    multi_rewrite_info: NotRequired[dict[str, Any] | None]
    multi_query_info: NotRequired[dict[str, Any] | None]
    hyde_info: NotRequired[dict[str, Any] | None]
    retrieval_questions: NotRequired[list[str]]
    cache_question: NotRequired[str]
    cache_info: NotRequired[dict[str, Any]]
    cache_hit: NotRequired[bool]
    citations: NotRequired[list[CitationItem]]
    citation_redaction: NotRequired[dict[str, Any]]
    contexts: NotRequired[list[str]]
    compression_info: NotRequired[dict[str, Any]]
    prompt: NotRequired[str]
    raw_answer: NotRequired[str]
    raw_answer_mode: NotRequired[str]
    answer: NotRequired[str]
    answer_mode: NotRequired[str]
    answer_redaction: NotRequired[dict[str, Any]]
    corrective_info: NotRequired[dict[str, Any]]
    retry_count: NotRequired[int]
    max_retry_count: NotRequired[int]
    self_rag_decision: NotRequired[str]
    retry_reason: NotRequired[str]
    retrieval_seed_question: NotRequired[str]


def init_query_graph_state(
    request: QueryRequest | ChatRequest,
    mode: WorkflowMode,
    task_spec: TaskSpec,
    task_run: TaskRun,
    *,
    max_retry_count: int = 0,
) -> QueryGraphState:
    """为一次 workflow 执行创建初始状态。

    Args:
        request: 当前查询或会话请求对象。
        mode: 本次工作流执行模式。

    Returns:
        已填充基础字段的初始工作流状态字典。
    """

    return {
        'mode': mode,
        'request': request,
        'task_spec': task_spec,
        'task_run': task_run,
        'started_at': perf_counter(),
        'result': None,
        'events': [],
        'run_events': [],
        # metadata 面向 trace / 持久化 / 调试界面，保存对排障有帮助的高层语义信息。
        'metadata': {
            'collection_name': request.collection_name,
            'task_type': task_spec.task_type,
            'task_objective': task_spec.objective,
            'task_steps': [step.step_id for step in task_spec.steps],
        },
        'error': None,
        'current_step_id': None,
        'completed_step_ids': [],
        'step_runtimes': {},
        'checkpoints': [],
        'context_bundles': {},
        'memory_records': [],
        'prompt_specs': [],
        'prompt_build_requests': [],
        'prompt_build_results': [],
        'grounded_context': None,
        'graph_subgraph': None,
        'retrieval_quality_report': None,
        'reflection_decision': None,
        'result_contract': None,
        'replayed_from_checkpoint_id': None,
        'graph_entry_route': None,
        'orchestration_next_route': None,
        'retry_count': 0,
        'max_retry_count': max(0, int(max_retry_count)),
    }

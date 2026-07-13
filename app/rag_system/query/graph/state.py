"""RAG 系统查询工作流状态模块。

定义 LangGraph 查询工作流在节点间共享的状态结构。
与 ``app/workflows/query_state.py`` 功能一致，但仅依赖 rag_system 自有类型，
不依赖主应用的 harness/models/capabilities。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal, TypedDict

try:
    from typing import NotRequired, Required
except ImportError:  # pragma: no cover - Python < 3.11
    from typing_extensions import NotRequired, Required

from app.rag_system.knowledge.contracts import RetrievalQualityReport
from app.rag_system.models.query import ChatRequest, CitationItem, QueryRequest, QueryResponse

SSEEvent = dict[str, Any]

# `WorkflowMode` 明确区分“请求语义”与“输出方式”，便于节点根据模式控制会话读写和 SSE 行为。
WorkflowMode = Literal['query', 'chat', 'query_stream', 'chat_stream']
# LangGraph 节点更新统一使用松散字典，具体字段约束由 `QueryGraphState` 和节点契约共同约束。
QueryGraphUpdate = dict[str, Any]

# LangGraph 图的合法入口路由列表。
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


class StepRuntimeRecord(TypedDict, total=False):
    """步骤运行时记录，描述单次步骤的执行态。"""
    step_id: str
    status: str                 # running / completed / skipped / failed
    started_at: float | None
    completed_at: float | None
    attempt_count: int
    exit_reason: str | None
    fallback_action_applied: str | None
    degraded: bool
    skipped: bool


class CheckpointRecord(TypedDict, total=False):
    """步骤 checkpoint 记录。"""
    checkpoint_id: str
    step_id: str
    step_index: int
    state_snapshot: dict[str, Any]
    created_at: float


class TaskRunEvent(TypedDict, total=False):
    """运行时事件记录。"""
    event_id: str
    name: str
    timestamp: float
    payload: dict[str, Any]


class TaskSpec(TypedDict, total=False):
    """任务规格说明（RAG 查询图的最小版本）。"""
    task_id: str
    steps: list['StepSpec']
    metadata: dict[str, Any]


class StepSpec(TypedDict, total=False):
    """步骤规格说明。"""
    step_id: str
    name: str
    description: str
    route_hint: str | None


class TaskRun(TypedDict, total=False):
    """任务运行记录（RAG 查询图的最小版本）。"""
    task_run_id: str
    task_id: str
    status: str
    current_step_id: str | None
    step_attempts: dict[str, int]
    step_runtimes: dict[str, StepRuntimeRecord]
    run_events: list[TaskRunEvent]
    metadata: dict[str, Any]


class QueryGraphState(TypedDict, total=False):
    """描述 query workflow 在节点间传递的共享状态。

    与 ``app/workflows/query_state.py`` 中的 ``QueryGraphState`` 保持字段兼容，
    但去掉了对主应用 ContextBundle/MemoryRecord/PromptSpec/ResultContract 等 harness 类型的依赖。
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
    retrieval_quality_report: NotRequired[RetrievalQualityReport | None]
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


def init_query_graph_state(
    mode: WorkflowMode,
    request: QueryRequest | ChatRequest,
    task_spec: TaskSpec,
    task_run: TaskRun,
) -> QueryGraphState:
    """初始化查询工作流状态。

    Args:
        mode: 工作流模式。
        request: 查询或聊天请求。
        task_spec: 任务规格。
        task_run: 任务运行记录。

    Returns:
        初始化的查询工作流状态。
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
        'metadata': {},
        'error': None,
    }

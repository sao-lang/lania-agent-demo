"""查询模型模块。

负责定义检索问答请求、聊天请求、引用项和响应体模型。这些模型处于 API、服务层和
工作流之间的共享边界，承载查询能力开关和标准化返回结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.capabilities.knowledge.contracts import RetrievalQualityReport
from app.models.runtime_contracts import GraphSubgraph, GroundedContext, MemoryRecord, PromptBuildRequest, PromptBuildResult, PromptSpec, ResultContract
from app.models.task import CheckpointRecord, ReflectionDecision, TaskRun, TaskSpec


class CitationItem(BaseModel):
    """回答中引用到的检索片段。

    该模型同时兼容普通向量检索、问题导向索引、父块回填和 GraphRAG 场景，因此字段较多，
    实际使用时不用每个字段都关心，按自己的场景取需要的那一部分就行。
    字段大致分成四类：基础引用信息、切块/索引来源、归档来源，以及 GraphRAG 轨迹信息。
    """

    # 最小可展示引用信息。
    chunk_id: str
    source: str
    file_path: str | None = None
    page: int | None = None
    score: float | None = None
    text: str

    # 检索命中与切块来源信息。
    matched_chunk_id: str | None = None
    index_kind: str | None = None
    node_level: str | None = None
    matched_via: list[str] | None = None
    chunking_strategy_requested: str | None = None
    chunking_strategy_effective: str | None = None
    chunking_prepared: bool | None = None
    source_segment_count: int | None = None
    child_chunk_id: str | None = None
    parent_chunk_id: str | None = None
    context_scope: str | None = None
    section_title: str | None = None
    hierarchy_path: str | None = None

    # 压缩包或归档导入来源信息。
    source_archive: str | None = None
    archive_member_path: str | None = None
    archive_member_display_path: str | None = None

    # GraphRAG 路径补充信息。
    graph_path: str | None = None
    graph_relation: str | None = None
    graph_start_entity: str | None = None
    graph_end_entity: str | None = None
    graph_path_hops: int | None = None


class QueryRequest(BaseModel):
    """标准检索问答请求体。

    这个模型把查询链路上常用的能力开关都收在一起了，比如改写、多查询、HyDE、
    上下文压缩、Corrective RAG 和 GraphRAG。
    它既是 API 入参，也是后续 runtime、service、评测链路的共同配置载体。
    """

    # 查询主体与访问范围。
    question: str
    collection_name: str
    top_k: int = Field(default=5, ge=1, le=20)
    session_id: str | None = None
    filters: dict | None = None
    permission_scope: str | None = None
    allowed_permissions: list[str] | None = None

    # 护栏、脱敏和上下文压缩开关。
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

    # 召回增强与纠错类策略。
    use_parent_chunk_retrieval: bool = False
    use_question_oriented_index: bool = False
    use_corrective_rag: bool = False
    use_graph_rag: bool = False
    graph_max_hops: int = Field(default=1, ge=1, le=3)
    graph_top_k: int = Field(default=5, ge=1, le=20)
    graph_entity_types: list[str] | None = None


class QueryResultArtifactContent(BaseModel):
    """query/chat 的结构化最终交付物。

    该模型把最终答案连同回答模式、引用、检索问题和附加元信息一起打包，是 artifact-first
    结果交付策略中的核心内容体。
    也就是说，真正可被消费的查询结果主要在这里，外层 artifact 只是统一包装壳。
    """

    answer: str
    answer_mode: str
    citations: list[CitationItem] = Field(default_factory=list)
    grounded: bool = False
    degraded: bool = False
    session_id: str | None = None
    retrieval_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryResultArtifact(BaseModel):
    """query/chat 的 artifact-first 交付对象。

    它把 `QueryResultArtifactContent` 包装成统一 artifact 结构，使查询结果和任务产物在交付层拥有
    一致的形状。
    这样 query/chat 输出就能和更通用的任务产物体系复用一套交付协议。
    """

    artifact_id: str
    artifact_type: str
    content: QueryResultArtifactContent
    created_at: datetime


class ChatRequest(QueryRequest):
    """带强制会话标识的对话请求体。

    跟 `QueryRequest` 比起来，它只是额外要求一定要带 `session_id`，这样才能接上会话历史。
    """

    session_id: str = Field(...)  # pyright: ignore[reportGeneralTypeIssues]


class QueryResponse(BaseModel):
    """检索问答返回结果。

    用来统一返回最终答案、引用列表、召回数量、耗时，还有可选的会话标识。
    `result_artifact` 存在时，意味着结果已经同步投影成 artifact-first 结构。
    """

    answer: str
    citations: list[CitationItem]
    retrieved_count: int
    latency_ms: int
    session_id: str | None = None
    result_artifact: QueryResultArtifact | None = None


class QueryRunEvent(BaseModel):
    """query runtime 的结构化运行事件。

    这类事件主要服务于运行历史、恢复、审计和排障，不等同于面向前端的 SSE 事件。
    """

    event_id: str
    name: str
    timestamp: datetime
    payload: dict[str, Any]


class QueryRunSummary(BaseModel):
    """query runtime 列表项。

    用于 query/chat 运行历史的概览展示，强调运行状态、模式、checkpoint 数量和结果摘要字段。
    该模型刻意保持轻量，适合列表页、监控页或恢复入口做概览展示。
    """

    run_id: str
    status: str
    mode: str
    task_type: str
    collection_name: str
    question: str
    created_at: datetime
    completed_at: datetime | None = None
    checkpoint_count: int = 0
    event_count: int = 0
    replayed_from_checkpoint_id: str | None = None
    last_checkpoint_id: str | None = None
    answer_mode: str | None = None
    result_artifact_id: str | None = None
    result_artifact_type: str | None = None
    latency_ms: int | None = None
    recoverable: bool = False


class QueryRunDetail(QueryRunSummary):
    """query runtime 详情。

    在 summary 基础上补齐请求载荷、TaskSpec、TaskRun、prompt 构建记录、grounding 结果、
    反思决策和最终响应，便于恢复与排障。
    它基本覆盖一次 query runtime 执行过程中可追踪的关键上下文，是最完整的运行时快照。
    """

    request_payload: dict[str, Any]
    task_spec: TaskSpec
    task_run: TaskRun
    checkpoints: list[CheckpointRecord]
    run_events: list[QueryRunEvent]
    memory_records: list[MemoryRecord] = Field(default_factory=list)
    prompt_specs: list[PromptSpec] = Field(default_factory=list)
    prompt_build_requests: list[PromptBuildRequest] = Field(default_factory=list)
    prompt_build_results: list[PromptBuildResult] = Field(default_factory=list)
    grounded_context: GroundedContext | None = None
    graph_subgraph: GraphSubgraph | None = None
    retrieval_quality_report: RetrievalQualityReport | None = None
    result_contract: ResultContract | None = None
    reflection_decision: ReflectionDecision | None = None
    result: QueryResponse | None = None


class QueryRunReplayRequest(BaseModel):
    """指定从哪个 checkpoint 重放 query runtime。"""

    checkpoint_id: str | None = None


class QueryRunAnalytics(BaseModel):
    """query runtime 的聚合统计视图。

    该模型聚焦运行观测层面的聚合指标，用于快速回答成功率、耗时分布、常见回答模式和退出原因等问题。
    """

    total_runs: int
    completed_runs: int
    failed_runs: int
    running_runs: int
    recoverable_runs: int
    replayed_runs: int
    average_latency_ms: float
    median_latency_ms: float
    mode_counts: dict[str, int]
    answer_mode_counts: dict[str, int]
    exit_reason_counts: dict[str, int]

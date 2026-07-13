"""共享类型模块。

集中定义各层都会复用的类型别名和运行期记录结构，主要给状态存储、服务层和工作流之间做
统一的数据约定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, TypedDict, Union

try:
    from typing import NotRequired
except ImportError:  # pragma: no cover - Python < 3.11
    from typing_extensions import NotRequired

try:
    from typing import TypeAlias
except ImportError:  # pragma: no cover - Python < 3.10
    from typing_extensions import TypeAlias

# 基础 JSON 值类型，用于约束运行期可序列化数据结构。
JsonPrimitive: TypeAlias = Optional[Union[str, int, float, bool]]
JsonValue: TypeAlias = Union[JsonPrimitive, list["JsonValue"], dict[str, "JsonValue"]]
FilterValue: TypeAlias = JsonValue
MetadataFilters: TypeAlias = dict[str, FilterValue]
# SSE 流式事件的统一字典结构，以及同步迭代读取时的返回协议。
SSEEvent: TypeAlias = dict[str, Any]
SSEEventResult: TypeAlias = tuple[bool, Optional[SSEEvent]]


class CollectionRecord(TypedDict):
    """集合在运行期内存状态中的记录结构。

    该结构用于描述知识库集合的配置和生命周期信息，是集合管理与持久化恢复的基础单元。
    """
    id: str
    name: str
    description: str | None
    status: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    created_at: datetime
    updated_at: datetime | None
    chunking_strategy: NotRequired[str]


class DocumentRecord(TypedDict):
    """文档在运行期内存状态中的记录结构。

    除了文件基础信息外，还承载索引状态、文档增强元数据和压缩包导入来源等信息。
    """
    doc_id: str
    file_name: str
    file_path: str
    file_type: str
    collection_name: str
    tags: list[str]
    checksum: str | None
    status: str
    chunk_ids: list[str]
    indexed_chunks: int
    created_at: datetime | None
    updated_at: datetime | None
    indexed_at: datetime | None
    error: NotRequired[str]
    document_title: NotRequired[str]
    document_summary: NotRequired[str | None]
    document_keywords: NotRequired[list[str]]
    document_hierarchy: NotRequired[str]
    year: NotRequired[str | None]
    quarter: NotRequired[str | None]
    version: NotRequired[str | None]
    permission: NotRequired[str | None]
    source_archive: NotRequired[str | None]
    archive_member_path: NotRequired[str | None]
    archive_member_display_path: NotRequired[str | None]


class SessionMessageRecord(TypedDict):
    """单条会话消息的运行期记录结构。

    该结构是会话历史最小粒度的存储单元，供会话摘要和对话回放复用。
    """
    role: str
    content: str
    created_at: datetime


class SessionRecord(TypedDict):
    """会话在运行期内存状态中的记录结构。

    用于保存完整消息历史、压缩摘要和最近更新时间等会话级状态。
    """
    messages: list[SessionMessageRecord]
    summary: str | None
    summary_updated_at: datetime | None
    compressed_message_count: int
    updated_at: datetime | None


class SemanticCacheRecord(TypedDict):
    """语义缓存记录结构。

    该结构覆盖问题向量、上下文签名、命中统计和缓存结果内容，供缓存命中与失效策略复用。
    """
    cache_id: str
    collection_name: str
    mode: str
    question: str
    normalized_question: str
    question_embedding: list[float]
    context_signature: str | None
    filters: MetadataFilters | None
    filters_signature: str
    strategy_signature: str
    answer: str
    answer_mode: str
    citations: list[dict[str, Any]]
    source_doc_ids: list[str]
    metadata: dict[str, Any]
    hit_count: int
    created_at: datetime
    updated_at: datetime
    last_hit_at: datetime | None


class TaskRecord(TypedDict):
    """任务在运行期存储中的记录结构。

    它汇总任务请求、计划、执行轨迹、记忆记录、产物引用与生命周期时间戳，是任务系统的核心状态载体。
    """
    task_id: str
    status: str
    request: dict[str, Any]
    task_spec: dict[str, Any] | None
    task_run: dict[str, Any] | None
    plan: dict[str, Any] | None
    plan_version: int
    current_step: str | None
    completed_steps: list[str]
    focus_aspects: list[str]
    evidence_pack_id: str | None
    artifact_ids: list[str]
    final_artifact_id: str | None
    metrics: dict[str, Any]
    failures: list[dict[str, Any]]
    plan_revisions: list[dict[str, Any]]
    task_memory_entries: list[dict[str, Any]]
    artifact_memory_entries: list[dict[str, Any]]
    reflection_entries: list[dict[str, Any]]
    tool_call_history: list[dict[str, Any]]
    sub_agent_runs: list[dict[str, Any]]
    run_events: list[dict[str, Any]]
    context_bundles: dict[str, dict[str, Any]]
    memory_records: list[dict[str, Any]]
    prompt_specs: list[dict[str, Any]]
    prompt_build_requests: list[dict[str, Any]]
    prompt_build_results: list[dict[str, Any]]
    grounded_context: dict[str, Any] | None
    graph_subgraph: dict[str, Any] | None
    retrieval_quality_report: dict[str, Any] | None
    result_contract: dict[str, Any] | None
    evaluation_scorecard: dict[str, Any] | None
    regression_result: dict[str, Any] | None
    retry_count: int
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    claimed_by: str | None
    created_at: datetime
    updated_at: datetime
    final_artifact: dict[str, Any] | None


class TaskRunRecord(TypedDict):
    """task runtime 在运行期存储中的记录结构。

    该结构面向单次任务运行实例，强调 checkpoint、恢复能力和运行结果追踪。
    """

    run_id: str
    task_id: str
    status: str
    task_type: str
    collection_name: str
    request_payload: dict[str, Any]
    task_spec: dict[str, Any]
    task_run: dict[str, Any]
    checkpoints: list[dict[str, Any]]
    run_events: list[dict[str, Any]]
    context_bundles: dict[str, dict[str, Any]]
    result_contract: dict[str, Any] | None
    final_artifact_id: str | None
    replayed_from_checkpoint_id: str | None
    last_checkpoint_id: str | None
    latency_ms: int | None
    recoverable: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class QueryRunRecord(TypedDict):
    """query/chat runtime 在运行期存储中的记录结构。

    用于记录一次 query 或 chat 运行的全过程，包括提示词构建、检索质量、反思决策和最终结果。
    """

    run_id: str
    status: str
    mode: str
    task_type: str
    collection_name: str
    request_payload: dict[str, Any]
    task_spec: dict[str, Any]
    task_run: dict[str, Any]
    checkpoints: list[dict[str, Any]]
    run_events: list[dict[str, Any]]
    memory_records: list[dict[str, Any]]
    prompt_specs: list[dict[str, Any]]
    prompt_build_requests: list[dict[str, Any]]
    prompt_build_results: list[dict[str, Any]]
    grounded_context: dict[str, Any] | None
    graph_subgraph: dict[str, Any] | None
    retrieval_quality_report: dict[str, Any] | None
    result_contract: dict[str, Any] | None
    prompt_specs: list[dict[str, Any]]
    prompt_build_requests: list[dict[str, Any]]
    prompt_build_results: list[dict[str, Any]]
    grounded_context: dict[str, Any] | None
    graph_subgraph: dict[str, Any] | None
    retrieval_quality_report: dict[str, Any] | None
    result_contract: dict[str, Any] | None
    reflection_decision: dict[str, Any] | None
    result: dict[str, Any] | None
    replayed_from_checkpoint_id: str | None
    last_checkpoint_id: str | None
    answer_mode: str | None
    result_artifact_id: str | None
    result_artifact_type: str | None
    latency_ms: int | None
    recoverable: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ArtifactRecord(TypedDict):
    """任务产物的运行期记录结构。

    用于保存任务输出物的版本、内容与审阅状态，支撑任务产物迭代与最终交付。
    """
    artifact_id: str
    task_id: str
    artifact_type: str
    version: int
    status: str
    content: dict[str, Any]
    review: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class GraphNodeRecord(TypedDict):
    """图谱节点的运行期记录结构。

    描述图谱实体节点的名称、类型、别名、来源文档和统计元数据。
    """
    node_id: str
    collection_name: str
    name: str
    normalized_name: str
    entity_type: str
    aliases: list[str]
    doc_ids: list[str]
    mention_count: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class GraphEdgeRecord(TypedDict):
    """图谱边的运行期记录结构。

    描述实体关系边、证据来源与权重信息，用于图谱检索和引用展示。
    """
    edge_id: str
    collection_name: str
    doc_id: str
    source_node_id: str
    source_name: str
    target_node_id: str
    target_name: str
    relation: str
    normalized_relation: str
    evidence_chunk_id: str
    evidence_text: str
    weight: float
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

"""内存状态模块。

负责定义应用运行期共享的内存状态容器，统一承载集合、文档、会话、反馈、图谱、任务和产物
等进程内数据。该模块位于服务基础层，是 API、服务和 workflow 读写运行态数据的共同入口。
"""

from dataclasses import dataclass, field
from typing import Any

from app.types import (
    ArtifactRecord,
    CollectionRecord,
    DocumentRecord,
    GraphEdgeRecord,
    GraphNodeRecord,
    QueryRunRecord,
    SemanticCacheRecord,
    SessionRecord,
    TaskRecord,
    TaskRunRecord,
)


@dataclass
class InMemoryState:
    """集中保存集合、文档、会话和评测等内存态数据。

    使用 dataclass 聚合所有运行期数据，可以让不同服务共享同一份进程内状态，同时在需要时
    由 `SQLiteStateStore` 统一做冷启动回填和持久化同步。
    """

    collections: dict[str, CollectionRecord] = field(default_factory=dict)
    documents: dict[str, DocumentRecord] = field(default_factory=dict)
    eval_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    feedback_items: list[dict[str, Any]] = field(default_factory=list)
    eval_candidates: list[dict[str, Any]] = field(default_factory=list)
    semantic_cache: dict[str, SemanticCacheRecord] = field(default_factory=dict)
    graph_nodes: dict[str, GraphNodeRecord] = field(default_factory=dict)
    graph_edges: dict[str, GraphEdgeRecord] = field(default_factory=dict)
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    task_runs: dict[str, TaskRunRecord] = field(default_factory=dict)
    query_runs: dict[str, QueryRunRecord] = field(default_factory=dict)

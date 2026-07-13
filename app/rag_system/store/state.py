"""RAG 系统独立内存状态模块。

使用独立的 ``RagState`` 替代主应用的 ``InMemoryState``，
只存储 query_run 等 RAG 相关数据，不存 task/agent 数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


class RagCollectionRecord(TypedDict, total=False):
    name: str
    documents: list[str]
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


class RagDocumentRecord(TypedDict, total=False):
    doc_id: str
    collection_name: str
    file_name: str
    file_type: str
    document_title: str
    document_summary: str
    indexed_chunks: int
    document_hierarchy: dict[str, Any]
    created_at: str
    metadata: dict[str, Any]


class RagSessionRecord(TypedDict, total=False):
    session_id: str
    collection_name: str
    messages: list[dict[str, Any]]
    summary: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


class RagQueryRunRecord(TypedDict, total=False):
    query_run_id: str
    question: str
    collection_name: str
    answer: str
    citations: list[dict[str, Any]]
    mode: str
    status: str
    created_at: str
    metadata: dict[str, Any]


class RagSemanticCacheRecord(TypedDict, total=False):
    cache_id: str
    question: str
    embedding: list[float]
    collection_name: str
    mode: str
    answer: str
    citations: list[dict[str, Any]]
    strategy_signature: str
    context_signature: str
    created_at: str
    expires_at: str
    hit_count: int


class RagGraphNodeRecord(TypedDict, total=False):
    node_id: str
    label: str
    collection_name: str
    chunk_ids: list[str]
    metadata: dict[str, Any]


class RagGraphEdgeRecord(TypedDict, total=False):
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    collection_name: str
    weight: float
    metadata: dict[str, Any]


@dataclass
class RagState:
    """RAG 系统独立内存状态。

    只存 query_run、collection、document、session 等 RAG 相关数据，
    不存 task/agent 数据。使用独立的 SQLite 文件持久化。
    """

    collections: dict[str, RagCollectionRecord] = field(default_factory=dict)
    documents: dict[str, RagDocumentRecord] = field(default_factory=dict)
    sessions: dict[str, RagSessionRecord] = field(default_factory=dict)
    query_runs: dict[str, RagQueryRunRecord] = field(default_factory=dict)
    semantic_cache: dict[str, RagSemanticCacheRecord] = field(default_factory=dict)
    graph_nodes: dict[str, RagGraphNodeRecord] = field(default_factory=dict)
    graph_edges: dict[str, RagGraphEdgeRecord] = field(default_factory=dict)

"""RAG 系统独立持久化模块。

使用独立的 SQLite 文件（rag_data.sqlite3），不依赖主应用的 app.sqlite3。
表结构独立，不污染主应用数据。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, cast

from app.rag_system.config.settings import RagSettings
from app.rag_system.store.state import RagState


class RagPersistence:
    """RAG 系统独立持久化。

    使用独立的 SQLite 文件，独立表空间。
    表：
    - rag_collections: 集合记录
    - rag_documents: 文档记录
    - rag_sessions: 会话记录
    - rag_query_runs: 查询运行记录
    - rag_semantic_cache: 语义缓存记录
    - rag_graph_nodes: 图谱节点记录
    - rag_graph_edges: 图谱边记录
    """

    def __init__(self, settings: RagSettings) -> None:
        """初始化持久化存储。

        Args:
            settings: RAG 系统配置，提供 SQLite 文件路径。
        """
        self.settings = settings
        self.db_path = settings.resolved_data_dir / settings.rag_data_path
        self._lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        """创建表结构（如不存在）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS rag_collections (
                    name TEXT PRIMARY KEY,
                    documents_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS rag_documents (
                    doc_id TEXT PRIMARY KEY,
                    collection_name TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_sessions (
                    session_id TEXT PRIMARY KEY,
                    collection_name TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_query_runs (
                    query_run_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    collection_name TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_semantic_cache (
                    cache_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    collection_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    strategy_signature TEXT NOT NULL DEFAULT '',
                    context_signature TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS rag_graph_nodes (
                    node_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    collection_name TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    collection_name TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rag_documents_collection
                    ON rag_documents(collection_name);
                CREATE INDEX IF NOT EXISTS idx_rag_query_runs_collection
                    ON rag_query_runs(collection_name);
                CREATE INDEX IF NOT EXISTS idx_rag_semantic_cache_collection
                    ON rag_semantic_cache(collection_name, mode);
                CREATE INDEX IF NOT EXISTS idx_rag_graph_nodes_collection
                    ON rag_graph_nodes(collection_name);
                CREATE INDEX IF NOT EXISTS idx_rag_graph_edges_collection
                    ON rag_graph_edges(collection_name);
            """)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """获取线程安全的数据库连接。"""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def load_into(self, state: RagState) -> None:
        """从 SQLite 读取已有状态并回填到内存。

        Args:
            state: 待回填的内存状态对象。
        """
        with self._connection() as conn:
            for row in conn.execute('SELECT * FROM rag_collections'):
                state.collections[row['name']] = {
                    'name': row['name'],
                    'documents': json.loads(row['documents_json']),
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'metadata': json.loads(row['metadata_json']),
                }
            for row in conn.execute('SELECT * FROM rag_documents'):
                data = json.loads(row['data_json'])
                state.documents[row['doc_id']] = {'doc_id': row['doc_id'], 'collection_name': row['collection_name'], **data}
            for row in conn.execute('SELECT * FROM rag_sessions'):
                data = json.loads(row['data_json'])
                state.sessions[row['session_id']] = {'session_id': row['session_id'], 'collection_name': row['collection_name'], **data}
            for row in conn.execute('SELECT * FROM rag_query_runs'):
                data = json.loads(row['data_json'])
                state.query_runs[row['query_run_id']] = {'query_run_id': row['query_run_id'], 'question': row['question'], 'collection_name': row['collection_name'], **data}
            for row in conn.execute('SELECT * FROM rag_semantic_cache ORDER BY created_at'):
                state.semantic_cache[row['cache_id']] = {
                    'cache_id': row['cache_id'],
                    'question': row['question'],
                    'embedding': json.loads(row['embedding_json']),
                    'collection_name': row['collection_name'],
                    'mode': row['mode'],
                    **json.loads(row['data_json']),
                    'strategy_signature': row['strategy_signature'],
                    'context_signature': row['context_signature'],
                    'created_at': row['created_at'],
                    'expires_at': row['expires_at'],
                    'hit_count': row['hit_count'],
                }
            for row in conn.execute('SELECT * FROM rag_graph_nodes'):
                data = json.loads(row['data_json'])
                state.graph_nodes[row['node_id']] = {'node_id': row['node_id'], 'label': row['label'], 'collection_name': row['collection_name'], **data}
            for row in conn.execute('SELECT * FROM rag_graph_edges'):
                data = json.loads(row['data_json'])
                state.graph_edges[row['edge_id']] = {
                    'edge_id': row['edge_id'],
                    'source_node_id': row['source_node_id'],
                    'target_node_id': row['target_node_id'],
                    'relation': row['relation'],
                    'collection_name': row['collection_name'],
                    'weight': row['weight'],
                    **data,
                }

    def ping(self) -> str:
        """探测 SQLite 文件是否可访问。"""
        try:
            with self._connection() as conn:
                conn.execute('SELECT 1')
            return 'up'
        except Exception:
            return 'degraded'

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _upsert(self, table: str, columns: list[str], values: list) -> None:
        placeholders = ','.join('?' for _ in columns)
        set_clause = ','.join(f'{c}=?' for c in columns)
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT({columns[0]}) DO UPDATE SET {set_clause[1:]}",
                values + values,
            )

    def upsert_collection(self, record: Mapping[str, Any]) -> None:
        docs_json = json.dumps(record.get('documents', []), ensure_ascii=False)
        meta_json = json.dumps(record.get('metadata', {}), ensure_ascii=False)
        self._upsert('rag_collections',
                     ['name', 'documents_json', 'created_at', 'updated_at', 'metadata_json'],
                     [record['name'], docs_json, record.get('created_at', self._now()),
                      record.get('updated_at', self._now()), meta_json])

    def delete_collection(self, name: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_collections WHERE name=?', [name])

    def upsert_document(self, record: Mapping[str, Any]) -> None:
        data = {k: v for k, v in record.items() if k not in ('doc_id', 'collection_name')}
        self._upsert('rag_documents',
                     ['doc_id', 'collection_name', 'data_json', 'created_at'],
                     [record['doc_id'], record.get('collection_name', ''),
                      json.dumps(data, ensure_ascii=False), record.get('created_at', self._now())])

    def delete_document(self, doc_id: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_documents WHERE doc_id=?', [doc_id])

    def upsert_session(self, session_id: str, data: Mapping[str, Any]) -> None:
        now = self._now()
        data_clean = {k: v for k, v in data.items() if k not in ('session_id', 'collection_name')}
        self._upsert('rag_sessions',
                     ['session_id', 'collection_name', 'data_json', 'created_at', 'updated_at'],
                     [session_id, data.get('collection_name', ''),
                      json.dumps(data_clean, ensure_ascii=False),
                      data.get('created_at', now), now])

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM rag_sessions WHERE session_id=?', [session_id]).fetchone()
            if row:
                result = dict(row)
                data = json.loads(result.pop('data_json'))
                result.update(data)
                return result
        return None

    def delete_session(self, session_id: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_sessions WHERE session_id=?', [session_id])

    def save_query_run(self, run_id: str, question: str, collection_name: str, data: Mapping[str, Any]) -> None:
        now = self._now()
        self._upsert('rag_query_runs',
                     ['query_run_id', 'question', 'collection_name', 'data_json', 'created_at'],
                     [run_id, question, collection_name, json.dumps(dict(data), ensure_ascii=False), now])

    def get_query_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM rag_query_runs WHERE query_run_id=?', [run_id]).fetchone()
            if row:
                result = dict(row)
                data = json.loads(result.pop('data_json'))
                result.update(data)
                return result
        return None

    def upsert_semantic_cache(self, record: Mapping[str, Any]) -> None:
        self._upsert('rag_semantic_cache',
                     ['cache_id', 'question', 'embedding_json', 'collection_name', 'mode',
                      'data_json', 'strategy_signature', 'context_signature',
                      'created_at', 'expires_at', 'hit_count'],
                     [record['cache_id'], record['question'],
                      json.dumps(record['embedding'], ensure_ascii=False),
                      record['collection_name'], record['mode'],
                      json.dumps({k: v for k, v in record.items()
                                  if k not in ('cache_id', 'question', 'embedding', 'collection_name',
                                               'mode', 'strategy_signature', 'context_signature',
                                               'created_at', 'expires_at', 'hit_count')},
                                 ensure_ascii=False),
                      record.get('strategy_signature', ''),
                      record.get('context_signature', ''),
                      record.get('created_at', self._now()),
                      record.get('expires_at', self._now()),
                      record.get('hit_count', 0)])

    def delete_semantic_cache(self, cache_id: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_semantic_cache WHERE cache_id=?', [cache_id])

    def delete_semantic_cache_for_collection(self, collection_name: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_semantic_cache WHERE collection_name=?', [collection_name])

    def upsert_graph_node(self, record: Mapping[str, Any]) -> None:
        data = {k: v for k, v in record.items() if k not in ('node_id', 'label', 'collection_name')}
        self._upsert('rag_graph_nodes',
                     ['node_id', 'label', 'collection_name', 'data_json', 'created_at'],
                     [record['node_id'], record['label'], record['collection_name'],
                      json.dumps(data, ensure_ascii=False), record.get('created_at', self._now())])

    def delete_graph_node(self, node_id: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_graph_nodes WHERE node_id=?', [node_id])

    def upsert_graph_edge(self, record: Mapping[str, Any]) -> None:
        data = {k: v for k, v in record.items()
                if k not in ('edge_id', 'source_node_id', 'target_node_id',
                             'relation', 'collection_name', 'weight')}
        self._upsert('rag_graph_edges',
                     ['edge_id', 'source_node_id', 'target_node_id', 'relation',
                      'collection_name', 'weight', 'data_json', 'created_at'],
                     [record['edge_id'], record['source_node_id'], record['target_node_id'],
                      record['relation'], record['collection_name'], record.get('weight', 1.0),
                      json.dumps(data, ensure_ascii=False), record.get('created_at', self._now())])

    def delete_graph_edge(self, edge_id: str) -> None:
        with self._connection() as conn:
            conn.execute('DELETE FROM rag_graph_edges WHERE edge_id=?', [edge_id])

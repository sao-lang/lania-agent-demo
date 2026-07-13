"""SQLite 状态存储模块。

负责把应用运行期状态序列化到本地 SQLite，并在启动时回填到 `InMemoryState`。该模块位于
服务基础层，承担轻量持久化、任务租约竞争和 JSON 结构编解码职责。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, cast

from app.core.config import Settings
from app.services.state import InMemoryState
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

class SQLiteStateStore:
    """负责把运行期状态同步到本地 SQLite。"""

    def __init__(self, settings: Settings) -> None:
        """初始化 SQLite 状态存储。

        Args:
            settings: 全局配置对象，提供 SQLite 文件路径。
        """
        self.settings = settings
        self.db_path = settings.sqlite_db_path
        self._lock = RLock()
        self._initialize()

    def load_into(self, state: InMemoryState) -> None:
        """从 SQLite 读取已有状态并回填到内存。

        Args:
            state: 待回填的内存状态对象。
        """
        state.collections = cast(dict[str, CollectionRecord], self._load_dict_table('collections', 'name'))
        state.documents = cast(dict[str, DocumentRecord], self._load_dict_table('documents', 'doc_id'))
        state.sessions = cast(dict[str, SessionRecord], self._load_dict_table('sessions', 'session_id'))
        state.eval_tasks = self._load_dict_table('eval_tasks', 'task_id')
        state.feedback_items = self._load_list_table('feedback_items')
        state.eval_candidates = self._load_list_table('eval_candidates')
        state.semantic_cache = cast(dict[str, SemanticCacheRecord], self._load_dict_table('semantic_cache_entries', 'cache_id'))
        state.graph_nodes = cast(dict[str, GraphNodeRecord], self._load_dict_table('graph_nodes', 'node_id'))
        state.graph_edges = cast(dict[str, GraphEdgeRecord], self._load_dict_table('graph_edges', 'edge_id'))
        state.tasks = cast(dict[str, TaskRecord], self._load_dict_table('tasks', 'task_id'))
        state.artifacts = cast(dict[str, ArtifactRecord], self._load_dict_table('artifacts', 'artifact_id'))
        state.task_runs = cast(dict[str, TaskRunRecord], self._load_dict_table('task_runs', 'run_id'))
        state.query_runs = cast(dict[str, QueryRunRecord], self._load_dict_table('query_runs', 'run_id'))

    def ping(self) -> str:
        """探测 SQLite 文件是否可访问。"""
        try:
            with self._connection() as connection:
                connection.execute('SELECT 1')
            return 'up'
        except Exception:
            return 'degraded'

    def upsert_collection(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条集合记录。"""
        self._upsert('collections', 'name', record['name'], record)

    def delete_collection(self, collection_name: str) -> None:
        """删除集合记录。"""
        self._delete('collections', 'name', collection_name)

    def upsert_document(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条文档记录。"""
        self._upsert('documents', 'doc_id', record['doc_id'], record)

    def delete_document(self, doc_id: str) -> None:
        """删除文档记录。"""
        self._delete('documents', 'doc_id', doc_id)

    def upsert_session(self, session_id: str, record: Mapping[str, Any]) -> None:
        """写入或更新单个会话。"""
        self._upsert('sessions', 'session_id', session_id, record)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """读取单条会话记录。"""
        return self._load_single('sessions', 'session_id', session_id)

    def delete_session(self, session_id: str) -> None:
        """删除单个会话。"""
        self._delete('sessions', 'session_id', session_id)

    # ── user_profiles ──────────────────────────────────────────────────────────

    def upsert_user_profile(self, user_id: str, record: Mapping[str, Any]) -> None:
        """写入或更新用户画像。"""
        self._upsert('user_profiles', 'user_id', user_id, record)

    def get_user_profile(self, user_id: str) -> dict[str, Any] | None:
        """读取单条用户画像。"""
        return self._load_single('user_profiles', 'user_id', user_id)

    def delete_user_profile(self, user_id: str) -> None:
        """删除用户画像。"""
        self._delete('user_profiles', 'user_id', user_id)

    # ── semantic_memory ────────────────────────────────────────────────────────

    def upsert_semantic_memory(self, memory_id: str, record: Mapping[str, Any]) -> None:
        """写入或更新语义记忆记录。"""
        self._upsert('semantic_memory', 'memory_id', memory_id, record)

    def get_semantic_memory(self, memory_id: str) -> dict[str, Any] | None:
        """读取单条语义记忆记录。"""
        return self._load_single('semantic_memory', 'memory_id', memory_id)

    def delete_semantic_memory(self, memory_id: str) -> None:
        """删除语义记忆记录。"""
        self._delete('semantic_memory', 'memory_id', memory_id)

    def upsert_eval_task(self, record: Mapping[str, Any]) -> None:
        """写入或更新单个评测任务。"""
        self._upsert('eval_tasks', 'task_id', record['task_id'], record)

    def delete_eval_task(self, task_id: str) -> None:
        """删除单个评测任务。"""
        self._delete('eval_tasks', 'task_id', task_id)

    def upsert_feedback_item(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条反馈记录。"""
        self._upsert('feedback_items', 'feedback_id', record['feedback_id'], record)

    def delete_feedback_item(self, feedback_id: str) -> None:
        """删除单条反馈记录。"""
        self._delete('feedback_items', 'feedback_id', feedback_id)

    def upsert_eval_candidate(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条评测候选记录。"""
        self._upsert('eval_candidates', 'candidate_id', record['candidate_id'], record)

    def delete_eval_candidate(self, candidate_id: str) -> None:
        """删除单条评测候选记录。"""
        self._delete('eval_candidates', 'candidate_id', candidate_id)

    def upsert_semantic_cache_entry(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条语义缓存记录。"""
        self._upsert('semantic_cache_entries', 'cache_id', record['cache_id'], record)

    def delete_semantic_cache_entry(self, cache_id: str) -> None:
        """删除单条语义缓存记录。"""
        self._delete('semantic_cache_entries', 'cache_id', cache_id)

    def upsert_graph_node(self, record: Mapping[str, Any]) -> None:
        """写入或更新单个图节点。"""
        self._upsert('graph_nodes', 'node_id', record['node_id'], record)

    def delete_graph_node(self, node_id: str) -> None:
        """删除单个图节点。"""
        self._delete('graph_nodes', 'node_id', node_id)

    def upsert_graph_edge(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条图边。"""
        self._upsert('graph_edges', 'edge_id', record['edge_id'], record)

    def delete_graph_edge(self, edge_id: str) -> None:
        """删除单条图边。"""
        self._delete('graph_edges', 'edge_id', edge_id)

    def upsert_task(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条任务记录。"""
        self._upsert('tasks', 'task_id', record['task_id'], record)

    def delete_task(self, task_id: str) -> None:
        """删除单条任务记录。"""
        self._delete('tasks', 'task_id', task_id)

    def upsert_artifact(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条产物记录。"""
        self._upsert('artifacts', 'artifact_id', record['artifact_id'], record)

    def delete_artifact(self, artifact_id: str) -> None:
        """删除单条产物记录。"""
        self._delete('artifacts', 'artifact_id', artifact_id)

    def upsert_query_run(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 query run 记录。"""
        self._upsert('query_runs', 'run_id', record['run_id'], record)

    def upsert_task_run(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 task run 记录。"""
        self._upsert('task_runs', 'run_id', record['run_id'], record)

    def delete_task_run(self, run_id: str) -> None:
        """删除单条 task run 记录。"""
        self._delete('task_runs', 'run_id', run_id)

    def get_task_run(self, run_id: str) -> dict[str, Any] | None:
        """读取单条 task run 记录。"""
        return self._load_single('task_runs', 'run_id', run_id)

    def list_task_runs(self) -> list[dict[str, Any]]:
        """读取全部 task run 记录。"""
        return self._load_table('task_runs')

    def delete_query_run(self, run_id: str) -> None:
        """删除单条 query run 记录。"""
        self._delete('query_runs', 'run_id', run_id)

    def get_query_run(self, run_id: str) -> dict[str, Any] | None:
        """读取单条 query run 记录。"""
        return self._load_single('query_runs', 'run_id', run_id)

    def list_query_runs(self) -> list[dict[str, Any]]:
        """读取全部 query run 记录。"""
        return self._load_table('query_runs')

    def upsert_managed_baseline(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 managed baseline 记录。"""
        self._upsert('managed_baselines', 'entry_id', record['entry_id'], record)

    def delete_managed_baseline(self, entry_id: str) -> None:
        """删除单条 managed baseline 记录。"""
        self._delete('managed_baselines', 'entry_id', entry_id)

    def list_managed_baselines(self) -> list[dict[str, Any]]:
        """读取全部 managed baseline 记录。"""
        return self._load_table('managed_baselines')

    def get_managed_baseline(self, entry_id: str) -> dict[str, Any] | None:
        """读取单条 managed baseline 记录。"""
        return self._load_single('managed_baselines', 'entry_id', entry_id)

    def append_managed_baseline_audit(self, record: Mapping[str, Any]) -> None:
        """追加一条 managed baseline 审计日志。"""
        self._upsert('managed_baseline_audits', 'audit_id', record['audit_id'], record)

    def list_managed_baseline_audits(self) -> list[dict[str, Any]]:
        """读取全部 managed baseline 审计日志。"""
        return self._load_table('managed_baseline_audits')

    def upsert_policy_profile(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条数据库策略画像。"""
        self._upsert('policy_profiles', 'profile_id', record['profile_id'], record)

    def delete_policy_profile(self, profile_id: str) -> None:
        """删除单条数据库策略画像。"""
        self._delete('policy_profiles', 'profile_id', profile_id)

    def list_policy_profiles(self) -> list[dict[str, Any]]:
        """读取全部数据库策略画像。"""
        return self._load_table('policy_profiles')

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """读取单条任务记录。"""
        return self._load_single('tasks', 'task_id', task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        """读取全部任务记录。"""
        return self._load_table('tasks')

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """读取单条产物记录。"""
        return self._load_single('artifacts', 'artifact_id', artifact_id)

    def list_artifacts(self) -> list[dict[str, Any]]:
        """读取全部产物记录。"""
        return self._load_table('artifacts')

    def list_artifacts_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """读取指定任务的全部产物记录。"""
        rows = self._load_table('artifacts')
        return [row for row in rows if row.get('task_id') == task_id]

    def claim_next_task(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """领取下一条可执行任务。

        Args:
            worker_id: 当前 worker 标识。
            lease_seconds: 任务租约时长。

        Returns:
            成功领取时返回任务载荷，否则返回 `None`。
        """

        now = datetime.now(timezone.utc)
        lease_expires_at = now + timedelta(seconds=max(lease_seconds, 1))
        with self._lock, self._connection() as connection:
            rows = connection.execute('SELECT task_id, payload FROM tasks ORDER BY rowid ASC').fetchall()
            for row in rows:
                payload = self._restore(json.loads(row['payload']))
                status = str(payload.get('status') or '')
                expires_at = payload.get('lease_expires_at')
                # queued 任务可直接领取；running 但租约过期的任务允许被重新接管。
                claimable = status == 'queued' or (
                    status == 'running' and isinstance(expires_at, datetime) and expires_at <= now
                )
                if not claimable:
                    continue
                payload['status'] = 'running'
                payload['claimed_by'] = worker_id
                payload['started_at'] = payload.get('started_at') or now
                payload['heartbeat_at'] = now
                payload['lease_expires_at'] = lease_expires_at
                payload['updated_at'] = now
                serialized = json.dumps(self._prepare(payload), ensure_ascii=False)
                connection.execute('UPDATE tasks SET payload = ? WHERE task_id = ?', (serialized, row['task_id']))
                connection.commit()
                return payload
        return None

    def touch_task_heartbeat(self, task_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """更新任务心跳与租约。

        Args:
            task_id: 任务 ID。
            worker_id: 当前 worker 标识。
            lease_seconds: 新租约时长。

        Returns:
            更新成功时返回任务载荷，否则返回 `None`。
        """

        now = datetime.now(timezone.utc)
        lease_expires_at = now + timedelta(seconds=max(lease_seconds, 1))
        with self._lock, self._connection() as connection:
            row = connection.execute('SELECT payload FROM tasks WHERE task_id = ?', (task_id,)).fetchone()
            if row is None:
                return None
            payload = self._restore(json.loads(row['payload']))
            if payload.get('claimed_by') != worker_id:
                return None
            payload['heartbeat_at'] = now
            payload['lease_expires_at'] = lease_expires_at
            payload['updated_at'] = now
            serialized = json.dumps(self._prepare(payload), ensure_ascii=False)
            connection.execute('UPDATE tasks SET payload = ? WHERE task_id = ?', (serialized, task_id))
            connection.commit()
            return payload

    def _initialize(self) -> None:
        """初始化数据库目录与表结构。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            # 所有业务对象统一落到“主键 + JSON payload”结构，便于在模型频繁演进时保持存储稳定。
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS collections (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS eval_tasks (
                    task_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS feedback_items (
                    feedback_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS eval_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS semantic_cache_entries (
                    cache_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    node_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS query_runs (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS managed_baselines (
                    entry_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS managed_baseline_audits (
                    audit_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS policy_profiles (
                    profile_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS semantic_memory (
                    memory_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            # ── 管理面资源表 ──────────────────────
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS skill_rules (
                    rule_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS agent_defs (
                    agent_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS prompts (
                    prompt_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    mcp_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                '''
            )
            # ── 系统提示词持久化缓存 ──────────────
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS prompt_cache (
                    cache_key TEXT PRIMARY KEY,   -- agent_id:skills_hash
                    payload TEXT NOT NULL         -- {system_prompt, agent_id, skills_hash, version, updated_at}
                )
                '''
            )

    def _connect(self) -> sqlite3.Connection:
        """创建新的 SQLite 连接。"""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """提供会自动关闭的 SQLite 连接。"""
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _upsert(self, table: str, id_column: str, record_id: str, payload: Mapping[str, Any]) -> None:
        """按主键写入 JSON 载荷。

        Args:
            table: 目标表名。
            id_column: 主键列名。
            record_id: 主键值。
            payload: 待写入记录。
        """
        serialized = json.dumps(self._prepare(payload), ensure_ascii=False)
        with self._lock, self._connection() as connection:
            connection.execute(
                f'INSERT INTO {table} ({id_column}, payload) VALUES (?, ?) '
                f'ON CONFLICT({id_column}) DO UPDATE SET payload = excluded.payload',
                (record_id, serialized),
            )
            connection.commit()

    def _delete(self, table: str, id_column: str, record_id: str) -> None:
        """按主键删除记录。

        Args:
            table: 目标表名。
            id_column: 主键列名。
            record_id: 主键值。
        """
        with self._lock, self._connection() as connection:
            connection.execute(f'DELETE FROM {table} WHERE {id_column} = ?', (record_id,))
            connection.commit()

    def _load_dict_table(self, table: str, id_column: str) -> dict[str, dict[str, Any]]:
        """加载以主键为索引的记录表。

        Args:
            table: 目标表名。
            id_column: 主键列名。

        Returns:
            以主键为 key 的记录字典。
        """
        with self._lock, self._connection() as connection:
            rows = connection.execute(f'SELECT {id_column}, payload FROM {table} ORDER BY rowid ASC').fetchall()
        return {str(row[id_column]): self._restore(json.loads(row['payload'])) for row in rows}

    def _load_table(self, table: str) -> list[dict[str, Any]]:
        """按插入顺序加载表中全部记录。

        Args:
            table: 目标表名。

        Returns:
            表中全部记录列表。
        """
        with self._lock, self._connection() as connection:
            rows = connection.execute(f'SELECT payload FROM {table} ORDER BY rowid ASC').fetchall()
        return [self._restore(json.loads(row['payload'])) for row in rows]

    def _load_single(self, table: str, id_column: str, record_id: str) -> dict[str, Any] | None:
        """按主键加载单条记录。

        Args:
            table: 目标表名。
            id_column: 主键列名。
            record_id: 主键值。

        Returns:
            命中时返回记录，否则返回 `None`。
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(
                f'SELECT payload FROM {table} WHERE {id_column} = ?',
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return self._restore(json.loads(row['payload']))

    def _load_list_table(self, table: str) -> list[dict[str, Any]]:
        """按插入顺序加载列表型记录表。

        Args:
            table: 目标表名。

        Returns:
            记录列表。
        """
        with self._lock, self._connection() as connection:
            rows = connection.execute(f'SELECT payload FROM {table} ORDER BY rowid ASC').fetchall()
        return [self._restore(json.loads(row['payload'])) for row in rows]

    def _prepare(self, value: Any) -> Any:
        """把 Python 对象转换为可 JSON 存储的结构。

        Args:
            value: 待序列化对象。

        Returns:
            可安全写入 JSON 的标准结构。
        """
        if isinstance(value, datetime):
            return {'__datetime__': value.isoformat()}
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._prepare(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._prepare(item) for item in value]
        if hasattr(value, 'model_dump'):
            return self._prepare(value.model_dump(mode='json'))
        return value

    def _restore(self, value: Any) -> Any:
        """把 JSON 结构恢复为运行期对象。"""
        if isinstance(value, list):
            return [self._restore(item) for item in value]
        if isinstance(value, dict):
            if set(value.keys()) == {'__datetime__'}:
                return datetime.fromisoformat(value['__datetime__'])
            return {key: self._restore(item) for key, item in value.items()}
        return value

    # ── Skills ─────────────────────────────────────────────────────────────────

    def upsert_skill(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 Skill 记录。"""
        self._upsert('skills', 'skill_id', record['id'], record)

    def delete_skill(self, skill_id: str) -> None:
        """删除单条 Skill 记录。"""
        self._delete('skills', 'skill_id', skill_id)

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        """读取单条 Skill 记录。"""
        return self._load_single('skills', 'skill_id', skill_id)

    def list_skills(self) -> list[dict[str, Any]]:
        """读取全部 Skill 记录。"""
        return self._load_table('skills')

    # ── Skill Rules ────────────────────────────────────────────────────────────

    def upsert_skill_rule(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 Skill Rule 记录。"""
        self._upsert('skill_rules', 'rule_id', record['id'], record)

    def delete_skill_rule(self, rule_id: str) -> None:
        """删除单条 Skill Rule 记录。"""
        self._delete('skill_rules', 'rule_id', rule_id)

    def get_skill_rule(self, rule_id: str) -> dict[str, Any] | None:
        """读取单条 Skill Rule 记录。"""
        return self._load_single('skill_rules', 'rule_id', rule_id)

    def list_skill_rules(self) -> list[dict[str, Any]]:
        """读取全部 Skill Rule 记录。"""
        return self._load_table('skill_rules')

    # ── Agent Definitions ──────────────────────────────────────────────────────

    def upsert_agent_def(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 Agent 定义记录。"""
        self._upsert('agent_defs', 'agent_id', record['id'], record)

    def delete_agent_def(self, agent_id: str) -> None:
        """删除单条 Agent 定义记录。"""
        self._delete('agent_defs', 'agent_id', agent_id)

    def get_agent_def(self, agent_id: str) -> dict[str, Any] | None:
        """读取单条 Agent 定义记录。"""
        return self._load_single('agent_defs', 'agent_id', agent_id)

    def list_agent_defs(self) -> list[dict[str, Any]]:
        """读取全部 Agent 定义记录。"""
        return self._load_table('agent_defs')

    # ── Prompts ────────────────────────────────────────────────────────────────

    def upsert_prompt(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 Prompt 记录。"""
        self._upsert('prompts', 'prompt_id', record['id'], record)

    def delete_prompt(self, prompt_id: str) -> None:
        """删除单条 Prompt 记录。"""
        self._delete('prompts', 'prompt_id', prompt_id)

    def get_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        """读取单条 Prompt 记录。"""
        return self._load_single('prompts', 'prompt_id', prompt_id)

    def list_prompts(self) -> list[dict[str, Any]]:
        """读取全部 Prompt 记录。"""
        return self._load_table('prompts')

    # ── MCP Servers ────────────────────────────────────────────────────────────

    def upsert_mcp_server(self, record: Mapping[str, Any]) -> None:
        """写入或更新单条 MCP Server 记录。"""
        self._upsert('mcp_servers', 'mcp_id', record['id'], record)

    def delete_mcp_server(self, mcp_id: str) -> None:
        """删除单条 MCP Server 记录。"""
        self._delete('mcp_servers', 'mcp_id', mcp_id)

    def get_mcp_server(self, mcp_id: str) -> dict[str, Any] | None:
        """读取单条 MCP Server 记录。"""
        return self._load_single('mcp_servers', 'mcp_id', mcp_id)

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        """读取全部 MCP Server 记录。"""
        return self._load_table('mcp_servers')

    # ── 系统提示词持久化缓存 ──────────────────

    def upsert_prompt_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        """写入或更新系统提示词缓存。"""
        self._upsert('prompt_cache', 'cache_key', cache_key, payload)

    def get_prompt_cache(self, cache_key: str) -> dict[str, Any] | None:
        """读取系统提示词缓存。"""
        return self._load_single('prompt_cache', 'cache_key', cache_key)

    def delete_prompt_cache(self, cache_key: str) -> None:
        """删除单条系统提示词缓存。"""
        self._delete('prompt_cache', 'cache_key', cache_key)

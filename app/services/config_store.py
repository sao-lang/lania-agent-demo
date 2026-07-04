"""通用配置持久化模块。

基于 SQLite 的 key-value 配置存储，为所有管理配置面提供统一持久化。
namespace 用于隔离不同配置域（llm, skill, agent_def 等）。

ConfigStore 自行管理 SQLite 建表和读写，不依赖外部存储。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Generator

from pydantic import BaseModel, Field


class ConfigItem(BaseModel):
    """一条配置记录。"""

    namespace: str
    key: str
    value: Any
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConfigStore:
    """通用配置持久化存储。

    在 SQLite 中维护 config_store 表，namespace + key 作为联合主键。
    值以 JSON 序列化存储。自带内存缓存提升读性能。
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else Path("data/app.sqlite3")
        self._lock = RLock()
        self._cache: dict[str, dict[str, ConfigItem]] = {}
        self._ensure_table()

    # ── 连接管理 ──────────────────────────────

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """获取 SQLite 连接。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        """确保配置表存在。"""
        try:
            with self._connection() as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS config_store (
                        namespace TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (namespace, key)
                    )"""
                )
        except Exception:
            pass

    # ── 读写接口 ──────────────────────────────

    def get(self, namespace: str, key: str) -> Any | None:
        """读取配置值。"""
        if namespace in self._cache and key in self._cache[namespace]:
            return self._cache[namespace][key].value

        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT value, updated_at FROM config_store "
                    "WHERE namespace = ? AND key = ?",
                    (namespace, key),
                ).fetchone()
                if row is not None:
                    value = json.loads(row["value"])
                    updated_at = datetime.fromisoformat(row["updated_at"])
                    item = ConfigItem(
                        namespace=namespace, key=key,
                        value=value, updated_at=updated_at,
                    )
                    self._set_cache(namespace, key, item)
                    return value
        except Exception:
            pass
        return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        """写入配置值。"""
        now = datetime.now(timezone.utc).isoformat()
        json_value = json.dumps(value, ensure_ascii=False, default=str)
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO config_store "
                    "(namespace, key, value, updated_at) VALUES (?, ?, ?, ?)",
                    (namespace, key, json_value, now),
                )
        except Exception:
            pass
        self._set_cache(
            namespace, key,
            ConfigItem(
                namespace=namespace, key=key, value=value,
                updated_at=datetime.now(timezone.utc),
            ),
        )

    def list(self, namespace: str) -> list[ConfigItem]:
        """列出某个命名空间下的所有配置。"""
        results: list[ConfigItem] = []
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT key, value, updated_at FROM config_store "
                    "WHERE namespace = ? ORDER BY key",
                    (namespace,),
                ).fetchall()
                for row in rows:
                    value = json.loads(row["value"])
                    updated_at = datetime.fromisoformat(row["updated_at"])
                    item = ConfigItem(
                        namespace=namespace, key=row["key"],
                        value=value, updated_at=updated_at,
                    )
                    results.append(item)
                    self._set_cache(namespace, row["key"], item)
        except Exception:
            pass
        return results

    def delete(self, namespace: str, key: str) -> None:
        """删除配置。"""
        try:
            with self._connection() as conn:
                conn.execute(
                    "DELETE FROM config_store WHERE namespace = ? AND key = ?",
                    (namespace, key),
                )
        except Exception:
            pass
        if namespace in self._cache:
            self._cache[namespace].pop(key, None)

    def _set_cache(self, namespace: str, key: str, item: ConfigItem) -> None:
        if namespace not in self._cache:
            self._cache[namespace] = {}
        self._cache[namespace][key] = item

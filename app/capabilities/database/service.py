"""本地 SQLite 数据库能力实现模块。

封装 SQLite 文件的只读连接、表结构探测与查询执行逻辑，并在执行前完成表名、
SQL 语句与多语句风险控制等基础校验。
"""


from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from app.capabilities.database.base import (
    DatabaseCapability,
    DatabaseColumnInfo,
    DatabaseDescribeTableRequest,
    DatabaseDescribeTableResult,
    DatabaseListTablesRequest,
    DatabaseListTablesResult,
    DatabaseQueryRequest,
    DatabaseQueryResult,
    DatabaseTableItem,
)
from app.core.config import Settings

_SAFE_TABLE_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class LocalSQLiteDatabaseCapability(DatabaseCapability):
    """基于本地 SQLite 文件的只读数据库能力。"""

    def __init__(self, db_path: Path) -> None:
        """初始化 SQLite 数据库文件路径并固定为绝对路径。"""
        self.db_path = db_path.resolve()

    def list_tables(self, request: DatabaseListTablesRequest) -> DatabaseListTablesResult:
        """列出数据库中的表和视图，并支持过滤系统表。"""
        sql = (
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name ASC"
            if request.include_system_tables
            else "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name ASC"
        )
        with self._connection() as connection:
            rows = connection.execute(sql).fetchmany(request.max_entries + 1)
        truncated = len(rows) > request.max_entries
        selected = rows[: request.max_entries]
        return DatabaseListTablesResult(
            db_path=str(self.db_path),
            tables=[DatabaseTableItem(name=str(row['name']), type=str(row['type'])) for row in selected],
            truncated=truncated,
        )

    def describe_table(self, request: DatabaseDescribeTableRequest) -> DatabaseDescribeTableResult:
        """读取指定表或视图的列结构信息。"""
        table_name = self._validate_table_name(request.table_name)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
                (table_name,),
            ).fetchone()
            if existing is None:
                raise LookupError(f'table not found: {table_name}')
            rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        return DatabaseDescribeTableResult(
            db_path=str(self.db_path),
            table_name=table_name,
            columns=[
                DatabaseColumnInfo(
                    name=str(row['name']),
                    data_type=str(row['type'] or ''),
                    not_null=bool(row['notnull']),
                    default_value=str(row['dflt_value']) if row['dflt_value'] is not None else None,
                    primary_key=bool(row['pk']),
                )
                for row in rows
            ],
        )

    def query(self, request: DatabaseQueryRequest) -> DatabaseQueryResult:
        """执行受限的只读 SQL 查询并返回结果集。"""
        sql = self._normalize_sql(request.sql)
        self._validate_read_only_sql(sql)
        with self._connection() as connection:
            cursor = connection.execute(sql)
            rows = cursor.fetchmany(request.max_rows + 1)
            columns = [item[0] for item in (cursor.description or [])]
        truncated = len(rows) > request.max_rows
        selected = rows[: request.max_rows]
        return DatabaseQueryResult(
            db_path=str(self.db_path),
            sql=sql,
            columns=columns,
            rows=[dict(row) for row in selected],
            truncated=truncated,
        )

    def _connect(self) -> sqlite3.Connection:
        """建立指向 SQLite 文件的只读连接。"""
        if not self.db_path.exists():
            raise FileNotFoundError(str(self.db_path))
        connection = sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """以上下文管理方式打开并关闭数据库连接。"""
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _validate_table_name(self, table_name: str) -> str:
        """校验表名仅包含安全字符，避免注入风险。"""
        if not _SAFE_TABLE_NAME_RE.match(table_name):
            raise ValueError(f'invalid table name: {table_name}')
        return table_name

    def _normalize_sql(self, raw_sql: str) -> str:
        """清理 SQL 两端空白，并去除尾部多余分号。"""
        sql = raw_sql.strip()
        if sql.endswith(';'):
            sql = sql[:-1].strip()
        return sql

    def _validate_read_only_sql(self, sql: str) -> None:
        """限制仅允许只读的 SELECT、WITH 或 PRAGMA 语句。"""
        lowered = sql.lower()
        if ';' in lowered:
            raise ValueError('multiple statements are not allowed')
        if lowered.startswith('select ') or lowered.startswith('with ') or lowered.startswith('pragma '):
            return
        raise ValueError('only read-only select/with/pragma queries are allowed')


def build_database_capability(settings: Settings) -> DatabaseCapability:
    """构建默认本地 SQLite database capability。"""
    return LocalSQLiteDatabaseCapability(settings.sqlite_db_path)

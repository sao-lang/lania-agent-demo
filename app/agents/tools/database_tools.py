"""数据库能力工具模块。

封装数据库表枚举、表结构读取和只读 SQL 查询三类工具，供任务运行时在统一
协议下访问底层 database capability。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.database import (
    DatabaseDescribeTableRequest,
    DatabaseDescribeTableResult,
    DatabaseListTablesRequest,
    DatabaseListTablesResult,
    DatabaseQueryRequest,
    DatabaseQueryResult,
)


class ListDatabaseTablesInput(BaseModel):
    """列出数据库表输入。"""

    include_system_tables: bool = False
    max_entries: int = Field(default=100, ge=1, le=500)


class DescribeDatabaseTableInput(BaseModel):
    """描述数据库表输入。"""

    table_name: str = Field(min_length=1)


class QueryDatabaseInput(BaseModel):
    """执行数据库只读查询输入。"""

    sql: str = Field(min_length=1)
    max_rows: int = Field(default=100, ge=1, le=1000)


class ListDatabaseTablesTool:
    """列出数据库表。"""

    name = 'list_database_tables'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ListDatabaseTablesInput
    output_model = DatabaseListTablesResult

    def run(self, payload: ListDatabaseTablesInput, context) -> DatabaseListTablesResult:
        """列出当前数据源下可见的数据表。"""

        if context.database is None:
            raise ToolExecutionError(
                code='database_capability_unavailable',
                message='database capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.database.list_tables(
            DatabaseListTablesRequest(
                include_system_tables=payload.include_system_tables,
                max_entries=payload.max_entries,
            )
        )


class DescribeDatabaseTableTool:
    """读取数据库表结构。"""

    name = 'describe_database_table'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = DescribeDatabaseTableInput
    output_model = DatabaseDescribeTableResult

    def run(self, payload: DescribeDatabaseTableInput, context) -> DatabaseDescribeTableResult:
        """读取指定表的结构化描述信息。"""

        if context.database is None:
            raise ToolExecutionError(
                code='database_capability_unavailable',
                message='database capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.database.describe_table(DatabaseDescribeTableRequest(table_name=payload.table_name))


class QueryDatabaseTool:
    """执行数据库只读查询。"""

    name = 'query_database'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = QueryDatabaseInput
    output_model = DatabaseQueryResult

    def run(self, payload: QueryDatabaseInput, context) -> DatabaseQueryResult:
        """执行受限的只读查询并返回结果。"""

        if context.database is None:
            raise ToolExecutionError(
                code='database_capability_unavailable',
                message='database capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.database.query(
            DatabaseQueryRequest(
                sql=payload.sql,
                max_rows=payload.max_rows,
            )
        )

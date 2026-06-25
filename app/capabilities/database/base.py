"""数据库能力契约模块。

定义列出表、读取表结构和执行只读 SQL 查询所需的模型与协议，为不同数据库
provider 保持一致的输入输出接口。
"""


from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator


class DatabaseListTablesRequest(BaseModel):
    """列出数据库表请求。"""

    include_system_tables: bool = False
    max_entries: int = Field(default=100, ge=1, le=500)


class DatabaseTableItem(BaseModel):
    """数据库表摘要。"""

    name: str
    type: str = 'table'


class DatabaseListTablesResult(BaseModel):
    """列出数据库表结果。"""

    db_path: str
    tables: list[DatabaseTableItem] = Field(default_factory=list)
    truncated: bool = False


class DatabaseDescribeTableRequest(BaseModel):
    """描述数据库表请求。"""

    table_name: str = Field(min_length=1)

    @field_validator('table_name')
    @classmethod
    def _strip_table_name(cls, value: str) -> str:
        """去除表名两端空白，并拒绝空表名。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned


class DatabaseColumnInfo(BaseModel):
    """数据库列信息。"""

    name: str
    data_type: str
    not_null: bool = False
    default_value: str | None = None
    primary_key: bool = False


class DatabaseDescribeTableResult(BaseModel):
    """描述数据库表结果。"""

    db_path: str
    table_name: str
    columns: list[DatabaseColumnInfo] = Field(default_factory=list)


class DatabaseQueryRequest(BaseModel):
    """数据库查询请求。"""

    sql: str = Field(min_length=1)
    max_rows: int = Field(default=100, ge=1, le=1000)

    @field_validator('sql')
    @classmethod
    def _strip_sql(cls, value: str) -> str:
        """去除 SQL 两端空白，并拒绝空查询语句。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned


class DatabaseQueryResult(BaseModel):
    """数据库查询结果。"""

    db_path: str
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class DatabaseCapability(Protocol):
    """稳定的数据库能力接口。"""

    db_path: Path

    def list_tables(self, request: DatabaseListTablesRequest) -> DatabaseListTablesResult:
        """列出数据库表。"""

    def describe_table(self, request: DatabaseDescribeTableRequest) -> DatabaseDescribeTableResult:
        """读取数据库表结构。"""

    def query(self, request: DatabaseQueryRequest) -> DatabaseQueryResult:
        """执行只读 SQL 查询。"""

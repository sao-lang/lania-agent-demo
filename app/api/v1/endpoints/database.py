"""Database capability 接口模块。

对外暴露数据库能力的健康检查、表列表、表结构和 SQL 查询接口。
该模块只负责 HTTP 层适配与错误包装，不直接实现数据库访问逻辑。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.capabilities.database import (
    DatabaseDescribeTableRequest,
    DatabaseDescribeTableResult,
    DatabaseListTablesRequest,
    DatabaseListTablesResult,
    DatabaseQueryRequest,
    DatabaseQueryResult,
)
from app.core.errors import AppError, error_responses

router = APIRouter()


@router.get('/health', responses=error_responses(500))
async def database_health(request: Request) -> dict:
    """返回 database capability 的健康状态。

    Args:
        request: 当前请求对象。

    Returns:
        capability 可用状态与数据库路径信息。
    """
    container = get_container(request)
    capability = container.local_database_capability
    return {
        'status': 'ok',
        'service': 'database_capability',
        'ready': True,
        'db_path': str(capability.db_path),
    }


@router.post('/list-tables', response_model=DatabaseListTablesResult, responses=error_responses(400, 422, 500))
async def list_database_tables(payload: DatabaseListTablesRequest, request: Request) -> DatabaseListTablesResult:
    """列出数据库中的表。

    Args:
        payload: 列表查询请求体。
        request: 当前请求对象。

    Returns:
        命中的表列表结果。
    """
    container = get_container(request)
    try:
        return container.local_database_capability.list_tables(payload)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise AppError(400, 'database_invalid_request', str(exc), {'include_system_tables': payload.include_system_tables}) from exc


@router.post('/describe-table', response_model=DatabaseDescribeTableResult, responses=error_responses(400, 404, 422, 500))
async def describe_database_table(
    payload: DatabaseDescribeTableRequest,
    request: Request,
) -> DatabaseDescribeTableResult:
    """读取指定表的结构描述。

    Args:
        payload: 表结构查询请求体。
        request: 当前请求对象。

    Returns:
        指定表的字段与约束描述结果。
    """
    container = get_container(request)
    try:
        return container.local_database_capability.describe_table(payload)
    except LookupError as exc:
        raise AppError(404, 'database_table_not_found', str(exc), {'table_name': payload.table_name}) from exc
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise AppError(400, 'database_invalid_request', str(exc), {'table_name': payload.table_name}) from exc


@router.post('/query', response_model=DatabaseQueryResult, responses=error_responses(400, 422, 500))
async def query_database(payload: DatabaseQueryRequest, request: Request) -> DatabaseQueryResult:
    """执行一次受控数据库查询。

    Args:
        payload: SQL 查询请求体。
        request: 当前请求对象。

    Returns:
        查询结果和元数据。
    """
    container = get_container(request)
    try:
        return container.local_database_capability.query(payload)
    except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
        raise AppError(400, 'database_invalid_query', str(exc), {'sql': payload.sql}) from exc

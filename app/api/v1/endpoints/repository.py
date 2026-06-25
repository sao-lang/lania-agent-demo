"""仓库能力接口模块。

对外暴露代码仓库能力的健康检查、文件列表、全文搜索和文件读取接口。
该模块是 repository capability 的 HTTP 适配层，负责请求入参与异常标准化。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.capabilities.repository import (
    RepositoryListFilesRequest,
    RepositoryListFilesResult,
    RepositoryReadFileRequest,
    RepositoryReadFileResult,
    RepositorySearchRequest,
    RepositorySearchResult,
)
from app.core.errors import AppError, error_responses

router = APIRouter()


@router.get('/health', responses=error_responses(500))
async def repository_health(request: Request) -> dict:
    """返回 repository capability 的健康状态。

    Args:
        request: 当前请求对象。

    Returns:
        capability 可用状态与仓库根目录信息。
    """
    container = get_container(request)
    capability = container.local_repository_capability
    return {
        'status': 'ok',
        'service': 'repository_capability',
        'ready': True,
        'root_path': str(capability.root_path),
    }


@router.post('/list-files', response_model=RepositoryListFilesResult, responses=error_responses(400, 422, 500))
async def list_repository_files(payload: RepositoryListFilesRequest, request: Request) -> RepositoryListFilesResult:
    """列出仓库内的文件。

    Args:
        payload: 文件列表查询请求体。
        request: 当前请求对象。

    Returns:
        命中的文件列表结果。
    """
    container = get_container(request)
    try:
        return container.local_repository_capability.list_files(payload)
    except (FileNotFoundError, PermissionError) as exc:
        raise AppError(400, 'repository_invalid_path', str(exc), {'path_prefix': payload.path_prefix}) from exc


@router.post('/search', response_model=RepositorySearchResult, responses=error_responses(400, 422, 500))
async def search_repository(payload: RepositorySearchRequest, request: Request) -> RepositorySearchResult:
    """在仓库中执行文本搜索。

    Args:
        payload: 文本搜索请求体。
        request: 当前请求对象。

    Returns:
        匹配结果列表。
    """
    container = get_container(request)
    try:
        return container.local_repository_capability.search_text(payload)
    except (FileNotFoundError, PermissionError) as exc:
        raise AppError(400, 'repository_invalid_path', str(exc), {'path_prefix': payload.path_prefix}) from exc


@router.post('/read-file', response_model=RepositoryReadFileResult, responses=error_responses(400, 422, 500))
async def read_repository_file(payload: RepositoryReadFileRequest, request: Request) -> RepositoryReadFileResult:
    """读取单个仓库文件内容。

    Args:
        payload: 文件读取请求体。
        request: 当前请求对象。

    Returns:
        文件内容与相关元数据。
    """
    container = get_container(request)
    try:
        return container.local_repository_capability.read_file(payload)
    except (FileNotFoundError, PermissionError) as exc:
        raise AppError(400, 'repository_invalid_path', str(exc), {'path': payload.path}) from exc

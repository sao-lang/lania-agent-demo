"""API Contract capability 接口模块。

对外暴露 API Contract 能力的健康检查、合同列表、接口搜索和合同读取入口。
该模块作为 capability 的 HTTP 包装层，主要负责参数接入和异常到标准响应的转换。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.capabilities.api_contract import (
    ApiContractListRequest,
    ApiContractListResult,
    ApiContractReadRequest,
    ApiContractReadResult,
    ApiContractSearchOperationsRequest,
    ApiContractSearchOperationsResult,
)
from app.core.errors import AppError, error_responses

router = APIRouter()


@router.get('/health', responses=error_responses(500))
async def api_contract_health(request: Request) -> dict:
    """返回 API contract capability 的健康状态。

    Args:
        request: 当前请求对象，用于读取容器中的本地 capability。

    Returns:
        当前 capability 的可用状态和根目录信息。
    """
    container = get_container(request)
    capability = container.local_api_contract_capability
    return {
        'status': 'ok',
        'service': 'api_contract_capability',
        'ready': True,
        'root_path': str(capability.root_path),
    }


@router.post('/list-contracts', response_model=ApiContractListResult, responses=error_responses(400, 422, 500))
async def list_api_contracts(payload: ApiContractListRequest, request: Request) -> ApiContractListResult:
    """列出满足条件的 API contract 文件。

    Args:
        payload: 列表查询请求体。
        request: 当前请求对象。

    Returns:
        命中的 contract 列表结果。
    """
    container = get_container(request)
    try:
        return container.local_api_contract_capability.list_contracts(payload)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise AppError(400, 'api_contract_invalid_path', str(exc), {'path_prefix': payload.path_prefix}) from exc


@router.post(
    '/search-operations',
    response_model=ApiContractSearchOperationsResult,
    responses=error_responses(400, 422, 500),
)
async def search_api_contract_operations(
    payload: ApiContractSearchOperationsRequest,
    request: Request,
) -> ApiContractSearchOperationsResult:
    """按关键字搜索 API contract 中的操作定义。

    Args:
        payload: 搜索请求体。
        request: 当前请求对象。

    Returns:
        匹配到的接口操作列表。
    """
    container = get_container(request)
    try:
        return container.local_api_contract_capability.search_operations(payload)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise AppError(400, 'api_contract_invalid_path', str(exc), {'path_prefix': payload.path_prefix}) from exc


@router.post('/read-contract', response_model=ApiContractReadResult, responses=error_responses(400, 404, 422, 500))
async def read_api_contract(payload: ApiContractReadRequest, request: Request) -> ApiContractReadResult:
    """读取指定 contract 或其中的单个操作定义。

    Args:
        payload: 合同读取请求体。
        request: 当前请求对象。

    Returns:
        解析后的 contract 内容或单个操作详情。
    """
    container = get_container(request)
    try:
        return container.local_api_contract_capability.read_contract(payload)
    except LookupError as exc:
        raise AppError(
            404,
            'api_contract_operation_not_found',
            str(exc),
            {'path': payload.path, 'method': payload.method, 'endpoint_path': payload.endpoint_path},
        ) from exc
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise AppError(400, 'api_contract_invalid_path', str(exc), {'path': payload.path}) from exc

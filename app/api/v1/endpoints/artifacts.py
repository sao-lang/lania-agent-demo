"""Artifact capability 接口模块。

对外暴露任务产物的健康检查、列表查询和单产物读取接口。
该模块主要把 capability 层能力映射为稳定的 HTTP 访问面。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.capabilities.artifact import ArtifactListRequest, ArtifactListResult
from app.core.errors import AppError, error_responses
from app.models.artifact import Artifact

router = APIRouter()


@router.get('/health', responses=error_responses(500))
async def artifact_health(request: Request) -> dict:
    """返回 artifact capability 的健康状态。

    Args:
        request: 当前请求对象。

    Returns:
        capability 是否就绪及当前产物数量等信息。
    """
    container = get_container(request)
    return {
        'status': 'ok',
        'service': 'artifact_capability',
        'ready': hasattr(container, 'local_artifact_capability'),
        'artifact_count': len(container.state.artifacts) if hasattr(container, 'state') else 0,
    }


@router.post('/list', response_model=ArtifactListResult, responses=error_responses(422, 500))
async def list_artifacts(payload: ArtifactListRequest, request: Request) -> ArtifactListResult:
    """按请求条件列出任务产物。

    Args:
        payload: 产物列表查询请求体。
        request: 当前请求对象。

    Returns:
        产物列表结果。
    """
    container = get_container(request)
    return container.local_artifact_capability.list_artifacts(payload)


@router.get('/{artifact_id}', response_model=Artifact, responses=error_responses(404, 500))
async def get_artifact(artifact_id: str, request: Request) -> Artifact:
    """读取单个任务产物详情。

    Args:
        artifact_id: 目标产物 ID。
        request: 当前请求对象。

    Returns:
        指定 ID 对应的产物对象。
    """
    container = get_container(request)
    artifact = container.local_artifact_capability.get_artifact(artifact_id)
    if artifact is None:
        raise AppError(404, 'artifact_not_found', f'artifact not found: {artifact_id}', {'artifact_id': artifact_id})
    return artifact

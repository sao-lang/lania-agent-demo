"""任务接口模块。

负责暴露文档分析任务的创建、查询、重试，以及工具 schema 和受控子 Agent schema 查询接口。
该模块属于 API 入口层，向上提供任务系统的 HTTP 入口，向下统一转发到 `TaskService`。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from app.api.deps import get_container
from app.core.errors import bad_request_error, error_responses
from app.agents.subagents import SubAgentSchema
from app.agents.tools.base import ToolSchema
from app.models.artifact import Artifact
from app.models.policy import (
    PolicyProfileCreateRequest,
    PolicyProfileItem,
    PolicyProfileListResponse,
    PolicyProfileUpdateRequest,
)
from app.models.task import TaskDetail, TaskListResponse, TaskRequest, TaskRunDetail, TaskRunReplayRequest, TaskRunSummary, TaskStatus

router = APIRouter()


@router.get('/tasks', response_model=TaskListResponse, responses=error_responses(404, 422, 500))
async def list_tasks(
    request: Request,
    status_filter: TaskStatus | None = Query(default=None, alias='status'),
    collection_name: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TaskListResponse:
    """列出任务，支持按状态和集合过滤。

    Args:
        request: 当前请求对象。
        status_filter: 可选任务状态过滤条件。
        collection_name: 可选集合名称过滤条件。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        任务列表响应对象。
    """

    container = get_container(request)
    return container.task_service.list_tasks(
        status=status_filter,
        collection_name=collection_name,
        limit=limit,
        offset=offset,
    )


@router.post(
    '/tasks',
    response_model=TaskDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(400, 404, 422, 500),
)
async def create_task(payload: TaskRequest, request: Request) -> TaskDetail:
    """创建并入队执行一个通用 task。

    Args:
        payload: 任务创建请求体。
        request: 当前请求对象。

    Returns:
        已入队任务的详情对象。
    """

    container = get_container(request)
    return container.task_service.create_task(payload)


@router.post(
    '/tasks/document-analysis',
    response_model=TaskDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(400, 404, 422, 500),
)
async def create_document_analysis_task(payload: TaskRequest, request: Request) -> TaskDetail:
    """创建并入队执行一个文档分析任务。

    Args:
        payload: 文档分析任务请求体。
        request: 当前请求对象。

    Returns:
        已入队的任务详情对象。
    """

    container = get_container(request)
    return container.task_service.create_document_analysis(payload)


@router.get('/tasks/tools', response_model=list[ToolSchema], responses=error_responses(500))
async def list_task_tools(request: Request) -> list[ToolSchema]:
    """返回任务工作流允许使用的工具与 schema。

    Args:
        request: 当前请求对象。

    Returns:
        可用工具 schema 列表。
    """

    container = get_container(request)
    return container.task_service.list_tool_schemas()


@router.get('/tasks/tools/{tool_name}', response_model=ToolSchema, responses=error_responses(404, 500))
async def get_task_tool_schema(tool_name: str, request: Request) -> ToolSchema:
    """读取单个工具 schema。

    Args:
        tool_name: 工具名称。
        request: 当前请求对象。

    Returns:
        指定工具的 schema 定义。
    """

    container = get_container(request)
    return container.task_service.get_tool_schema(tool_name)


@router.get('/tasks/sub-agents', response_model=list[SubAgentSchema], responses=error_responses(500))
async def list_task_subagents(request: Request) -> list[SubAgentSchema]:
    """返回任务工作流使用的受控子代理 schema。

    Args:
        request: 当前请求对象。

    Returns:
        受控子代理 schema 列表。
    """

    container = get_container(request)
    return container.task_service.list_subagent_schemas()


@router.get('/tasks/sub-agents/{agent_name}', response_model=SubAgentSchema, responses=error_responses(404, 500))
async def get_task_subagent_schema(agent_name: str, request: Request) -> SubAgentSchema:
    """读取单个受控子代理 schema。

    Args:
        agent_name: 子代理名称。
        request: 当前请求对象。

    Returns:
        指定子代理的 schema 定义。
    """

    container = get_container(request)
    return container.task_service.get_subagent_schema(agent_name)


@router.get(
    '/tasks/document-analysis/policies',
    response_model=PolicyProfileListResponse,
    responses=error_responses(400, 500),
)
async def list_document_analysis_policies(
    request: Request,
    organization_id: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PolicyProfileListResponse:
    """列出数据库化策略画像。

    Args:
        request: 当前请求对象。
        organization_id: 可选组织 ID 过滤条件。
        tenant_id: 可选租户 ID 过滤条件。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        策略画像分页列表结果。
    """
    container = get_container(request)
    return container.task_service.list_policy_profiles(
        organization_id=organization_id,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    '/tasks/document-analysis/policies',
    response_model=PolicyProfileItem,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 422, 500),
)
async def create_document_analysis_policy(
    payload: PolicyProfileCreateRequest,
    request: Request,
) -> PolicyProfileItem:
    """创建数据库化策略画像。

    Args:
        payload: 策略画像创建请求体。
        request: 当前请求对象。

    Returns:
        新建后的策略画像对象。
    """
    container = get_container(request)
    return container.task_service.create_policy_profile(payload)


@router.patch(
    '/tasks/document-analysis/policies/{profile_id}',
    response_model=PolicyProfileItem,
    responses=error_responses(400, 404, 422, 500),
)
async def update_document_analysis_policy(
    profile_id: str,
    payload: PolicyProfileUpdateRequest,
    request: Request,
) -> PolicyProfileItem:
    """更新数据库化策略画像。

    Args:
        profile_id: 待更新画像 ID。
        payload: 策略画像更新请求体。
        request: 当前请求对象。

    Returns:
        更新后的策略画像对象。
    """
    container = get_container(request)
    return container.task_service.update_policy_profile(profile_id, payload)


@router.delete(
    '/tasks/document-analysis/policies/{profile_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(400, 404, 500),
)
async def delete_document_analysis_policy(profile_id: str, request: Request) -> None:
    """删除数据库化策略画像。

    Args:
        profile_id: 待删除画像 ID。
        request: 当前请求对象。
    """
    container = get_container(request)
    container.task_service.delete_policy_profile(profile_id)


@router.get('/tasks/runs', response_model=list[TaskRunSummary], responses=error_responses(500))
async def list_task_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    collection_name: str | None = Query(default=None),
    recoverable_only: bool = Query(default=False),
) -> list[TaskRunSummary]:
    """列出 task runtime 历史。

    Args:
        request: 当前请求对象。
        limit: 返回数量上限。
        offset: 分页偏移量。
        status: 可选运行状态过滤条件。
        collection_name: 可选集合名称过滤条件。
        recoverable_only: 是否仅返回可恢复运行。

    Returns:
        任务运行摘要列表。
    """
    container = get_container(request)
    return container.task_service.list_task_runs(
        limit=limit,
        offset=offset,
        status=status,
        collection_name=collection_name,
        recoverable_only=recoverable_only,
    )


@router.get('/tasks/runs/{run_id}', response_model=TaskRunDetail, responses=error_responses(404, 500))
async def get_task_run(run_id: str, request: Request) -> TaskRunDetail:
    """读取单个 task runtime 详情。

    Args:
        run_id: 任务运行 ID。
        request: 当前请求对象。

    Returns:
        指定运行的完整详情。
    """
    container = get_container(request)
    return container.task_service.get_task_run(run_id)


@router.post('/tasks/runs/{run_id}/replay', response_model=TaskRunDetail, responses=error_responses(400, 404, 500))
async def replay_task_run(run_id: str, payload: TaskRunReplayRequest, request: Request) -> TaskRunDetail:
    """从指定 task runtime checkpoint 发起 replay。

    Args:
        run_id: 任务运行 ID。
        payload: replay 请求体。
        request: 当前请求对象。

    Returns:
        新生成的 replay 运行详情。
    """
    container = get_container(request)
    try:
        return container.task_service.replay_task_run(run_id, checkpoint_id=payload.checkpoint_id)
    except ValueError as exc:
        raise bad_request_error('invalid_checkpoint', str(exc)) from exc


@router.post('/tasks/runs/{run_id}/resume', response_model=TaskRunDetail, responses=error_responses(400, 404, 500))
async def resume_task_run(run_id: str, request: Request) -> TaskRunDetail:
    """从最近 checkpoint 恢复一个可恢复的 task runtime。

    Args:
        run_id: 任务运行 ID。
        request: 当前请求对象。

    Returns:
        恢复后的任务运行详情。
    """
    container = get_container(request)
    return container.task_service.resume_task_run(run_id)


@router.get('/tasks/{task_id}', response_model=TaskDetail, responses=error_responses(404, 500))
async def get_task_detail(task_id: str, request: Request) -> TaskDetail:
    """读取任务详情。

    Args:
        task_id: 任务 ID。
        request: 当前请求对象。

    Returns:
        任务详情对象。
    """

    container = get_container(request)
    return container.task_service.get_task(task_id)


@router.get('/tasks/{task_id}/artifacts', response_model=list[Artifact], responses=error_responses(404, 500))
async def list_task_artifacts(task_id: str, request: Request) -> list[Artifact]:
    """列出任务产物。

    Args:
        task_id: 任务 ID。
        request: 当前请求对象。

    Returns:
        与任务关联的产物列表。
    """

    container = get_container(request)
    return container.task_service.list_artifacts(task_id)


@router.post(
    '/tasks/{task_id}/retry',
    response_model=TaskDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(404, 500),
)
async def retry_task(task_id: str, request: Request) -> TaskDetail:
    """重新入队执行指定任务。

    Args:
        task_id: 任务 ID。
        request: 当前请求对象。

    Returns:
        已重置并重新入队的任务详情对象。
    """

    container = get_container(request)
    return container.task_service.retry_task(task_id)

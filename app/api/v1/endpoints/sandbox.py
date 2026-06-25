"""Sandbox worker 接口模块。

这些接口暴露高风险工具的独立执行面，供 `remote_http` sandbox provider 调用。
实现上固定走容器中的本地 sandbox engine，避免远程 provider 配置后递归回调自己。
"""

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.core.errors import AppError, error_responses
from app.harness.sandbox import (
    SandboxExecutionRequest,
    SandboxExecutionResponse,
    SandboxWorkerToolCatalog,
    SandboxWorkerToolSchema,
)
from app.agents.tools.base import ToolExecutionError

router = APIRouter()


@router.get('/health', responses=error_responses(500))
async def sandbox_health(request: Request) -> dict:
    """返回 sandbox worker 的健康与就绪状态。

    Args:
        request: 当前请求对象。

    Returns:
        provider 配置、工具支持情况和 readiness 信息。
    """
    container = get_container(request)
    settings = container.settings
    catalog = container.local_sandbox_engine.list_worker_tools()
    return {
        'status': 'ok',
        'service': 'sandbox_worker',
        'provider': settings.sandbox_executor_provider,
        'ready': True,
        'remote_provider_enabled': settings.sandbox_executor_provider == 'remote_http',
        'base_url_configured': bool(settings.sandbox_executor_base_url),
        'auth_configured': bool(settings.sandbox_executor_auth_token),
        'timeout_seconds': settings.sandbox_executor_timeout_seconds,
        'allow_local_fallback': settings.sandbox_executor_allow_local_fallback,
        'circuit_breaker_threshold': settings.remote_provider_circuit_breaker_threshold,
        'circuit_breaker_cooldown_seconds': settings.remote_provider_circuit_breaker_cooldown_seconds,
        'supported_tools_count': len(catalog.tools),
        'supported_tools': [tool.tool_name for tool in catalog.tools],
    }


@router.get('/tools', response_model=SandboxWorkerToolCatalog, responses=error_responses(500))
async def list_sandbox_tools(request: Request) -> SandboxWorkerToolCatalog:
    """返回 sandbox worker 当前支持的工具目录。

    Args:
        request: 当前请求对象。

    Returns:
        当前 worker 支持的工具清单与 schema 摘要。
    """
    container = get_container(request)
    return container.local_sandbox_engine.list_worker_tools()


@router.get('/tools/{tool_name}', response_model=SandboxWorkerToolSchema, responses=error_responses(400, 404, 500))
async def get_sandbox_tool(tool_name: str, request: Request) -> SandboxWorkerToolSchema:
    """返回单个 sandbox worker 工具 schema。

    Args:
        tool_name: 工具名称。
        request: 当前请求对象。

    Returns:
        指定工具的 schema 描述。
    """
    container = get_container(request)
    try:
        return container.local_sandbox_engine.describe_worker_tool(tool_name)
    except ToolExecutionError as exc:
        raise AppError(status_code=404, code=exc.code, message=exc.message, details=exc.details) from exc


@router.post('/execute-tool', response_model=SandboxExecutionResponse, responses=error_responses(400, 422, 500))
async def execute_tool(payload: SandboxExecutionRequest, request: Request) -> SandboxExecutionResponse:
    """在本地隔离环境中执行指定 sandbox 工具。

    Args:
        payload: 工具执行请求体。
        request: 当前请求对象。

    Returns:
        工具执行结果及其序列化数据。
    """
    container = get_container(request)
    try:
        worker_tool = container.local_sandbox_engine.describe_worker_tool(payload.tool_name)
        result = container.local_sandbox_engine.execute_local_isolated(
            tool_name=payload.tool_name,
            payload=payload.payload,
            timeout_ms=payload.timeout_ms,
            output_model=container.local_sandbox_engine.worker_registry.get(worker_tool.tool_name).output_model,
        )
    except ToolExecutionError as exc:
        raise AppError(
            status_code=400 if exc.error_type in {'validation_error', 'permission_error'} else 500,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ) from exc
    return SandboxExecutionResponse(
        tool_name=payload.tool_name,
        sandbox_mode='process_isolated',
        data=result.model_dump(mode='json'),
    )

"""Knowledge Capability 服务接口模块。

这些接口暴露 capability 层的稳定能力面，供远程 provider 或统一适配层调用。
实现上固定走容器中的本地 knowledge capability，避免 provider 配成 `remote_http`
后再次回调自己造成递归。
"""

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.capabilities.knowledge import (
    DocumentContextCall,
    DocumentContextResult,
    GroundedAnswerCall,
    GroundedAnswerResult,
    KnowledgeSearchCall,
    RemoteKnowledgeProviderError,
)
from app.core.errors import AppError, error_responses
from app.models.artifact import EvidencePack

router = APIRouter()


@router.get('/health', responses=error_responses(500))
async def knowledge_health(request: Request) -> dict:
    """返回 knowledge capability worker 的健康与就绪状态。

    Args:
        request: 当前请求对象。

    Returns:
        provider 配置、认证开关和 readiness 信息。
    """
    container = get_container(request)
    settings = container.settings
    return {
        'status': 'ok',
        'service': 'knowledge_capability',
        'provider': settings.knowledge_capability_provider,
        'ready': True,
        'remote_provider_enabled': settings.knowledge_capability_provider == 'remote_http',
        'base_url_configured': bool(settings.knowledge_capability_base_url),
        'auth_configured': bool(settings.knowledge_capability_auth_token),
        'timeout_seconds': settings.knowledge_capability_timeout_seconds,
        'allow_local_fallback': settings.knowledge_capability_allow_local_fallback,
        'circuit_breaker_threshold': settings.remote_provider_circuit_breaker_threshold,
        'circuit_breaker_cooldown_seconds': settings.remote_provider_circuit_breaker_cooldown_seconds,
    }


@router.post('/document-context', response_model=DocumentContextResult, responses=error_responses(422, 500))
async def load_document_context(payload: DocumentContextCall, request: Request) -> DocumentContextResult:
    """按请求加载文档上下文片段。

    Args:
        payload: 文档上下文加载请求体。
        request: 当前请求对象。

    Returns:
        组装后的文档上下文结果。
    """
    container = get_container(request)
    capability = container.local_knowledge_capability
    try:
        return capability.load_document_context(payload.request)
    except RemoteKnowledgeProviderError as exc:
        raise AppError(exc.status_code, exc.code, exc.message, exc.details) from exc


@router.post('/search', response_model=EvidencePack, responses=error_responses(422, 500))
async def retrieve_evidence(payload: KnowledgeSearchCall, request: Request) -> EvidencePack:
    """检索知识证据包。

    Args:
        payload: 知识检索请求体。
        request: 当前请求对象。

    Returns:
        可供回答或任务分析使用的证据包。
    """
    container = get_container(request)
    capability = container.local_knowledge_capability
    try:
        return capability.retrieve_evidence(payload.request, trace_context=payload.trace_context)
    except RemoteKnowledgeProviderError as exc:
        raise AppError(exc.status_code, exc.code, exc.message, exc.details) from exc


@router.post('/grounded-answer', response_model=GroundedAnswerResult, responses=error_responses(422, 500))
async def grounded_answer(payload: GroundedAnswerCall, request: Request) -> GroundedAnswerResult:
    """基于证据生成 grounded answer。

    Args:
        payload: grounded answer 请求体。
        request: 当前请求对象。

    Returns:
        带引用与附加信息的回答结果。
    """
    container = get_container(request)
    capability = container.local_knowledge_capability
    try:
        return capability.grounded_answer(payload.request, trace_context=payload.trace_context)
    except RemoteKnowledgeProviderError as exc:
        raise AppError(exc.status_code, exc.code, exc.message, exc.details) from exc

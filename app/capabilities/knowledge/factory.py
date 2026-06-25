"""Knowledge 能力工厂模块。

负责按配置选择本地或远程知识能力 provider，并统一组织 fallback、模型路由与
provider 注册逻辑。
"""


from __future__ import annotations

from typing import Any, Protocol

from app.capabilities.knowledge.base import KnowledgeCapability
from app.capabilities.knowledge.remote import RemoteKnowledgeCapability
from app.capabilities.knowledge.service import DefaultKnowledgeCapability
from app.core.config import Settings
from app.harness.model_router import ModelRouter


class KnowledgeCapabilityProvider(Protocol):
    """描述一个可构建知识能力实例的 provider。"""

    name: str

    def build(
        self,
        *,
        settings: Settings,
        state: Any,
        retrieval: Any,
        vector_store: Any,
        llm: Any | None,
        model_router: ModelRouter | None = None,
        local_fallback_capability: KnowledgeCapability | None = None,
    ) -> KnowledgeCapability:
        """构建当前 provider 对应的知识能力实例。"""

        ...


class DefaultKnowledgeCapabilityProvider:
    """默认的本地 Knowledge Capability provider。"""

    name = 'default'

    def build(
        self,
        *,
        settings: Settings,
        state: Any,
        retrieval: Any,
        vector_store: Any,
        llm: Any | None,
        model_router: ModelRouter | None = None,
        local_fallback_capability: KnowledgeCapability | None = None,
    ) -> KnowledgeCapability:
        """返回默认本地 Knowledge 能力实现。"""
        return DefaultKnowledgeCapability(
            state,
            retrieval,
            vector_store,
            llm,
            model_router=model_router,
        )


class RemoteHttpKnowledgeCapabilityProvider:
    """通过远程 HTTP 服务提供 Knowledge Capability。"""

    name = 'remote_http'

    def build(
        self,
        *,
        settings: Settings,
        state: Any,
        retrieval: Any,
        vector_store: Any,
        llm: Any | None,
        model_router: ModelRouter | None = None,
        local_fallback_capability: KnowledgeCapability | None = None,
    ) -> KnowledgeCapability:
        """构建带远程调用与本地回退策略的 Knowledge 能力实例。"""
        if not settings.knowledge_capability_base_url:
            raise ValueError('KNOWLEDGE_CAPABILITY_BASE_URL is required when provider=remote_http')
        fallback_capability = None
        if settings.knowledge_capability_allow_local_fallback:
            fallback_capability = local_fallback_capability or DefaultKnowledgeCapabilityProvider().build(
                settings=settings,
                state=state,
                retrieval=retrieval,
                vector_store=vector_store,
                llm=llm,
                model_router=model_router,
            )
        return RemoteKnowledgeCapability(
            base_url=settings.knowledge_capability_base_url,
            api_prefix=settings.api_prefix,
            timeout_seconds=settings.knowledge_capability_timeout_seconds,
            auth_token=settings.knowledge_capability_auth_token,
            fallback_capability=fallback_capability,
            allow_local_fallback=settings.knowledge_capability_allow_local_fallback,
            circuit_breaker_threshold=settings.remote_provider_circuit_breaker_threshold,
            circuit_breaker_cooldown_seconds=settings.remote_provider_circuit_breaker_cooldown_seconds,
        )


class KnowledgeCapabilityRegistry:
    """按 provider 名称管理知识能力构建器。"""

    def __init__(self) -> None:
        """初始化知识能力 provider 注册表。"""
        self._providers: dict[str, KnowledgeCapabilityProvider] = {}

    def register(self, provider: KnowledgeCapabilityProvider) -> None:
        """注册一个可按名称检索的知识能力 provider。"""
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> KnowledgeCapabilityProvider:
        """按名称返回已注册的知识能力 provider。"""
        return self._providers[provider_name]

    def has(self, provider_name: str) -> bool:
        """判断指定名称的知识能力 provider 是否已注册。"""
        return provider_name in self._providers


def build_default_knowledge_capability_registry() -> KnowledgeCapabilityRegistry:
    """构造包含默认本地与远程 HTTP provider 的知识能力注册表。"""
    registry = KnowledgeCapabilityRegistry()
    registry.register(DefaultKnowledgeCapabilityProvider())
    registry.register(RemoteHttpKnowledgeCapabilityProvider())
    return registry


def build_knowledge_capability(
    *,
    settings: Settings,
    state: Any,
    retrieval: Any,
    vector_store: Any,
    llm: Any | None,
    provider_name: str | None = None,
    registry: KnowledgeCapabilityRegistry | None = None,
    model_router: ModelRouter | None = None,
    local_fallback_capability: KnowledgeCapability | None = None,
) -> KnowledgeCapability:
    """按配置选择 provider 并构建知识能力实例。"""
    effective_registry = registry or build_default_knowledge_capability_registry()
    effective_provider = provider_name or settings.knowledge_capability_provider
    provider = effective_registry.get(effective_provider)
    return provider.build(
        settings=settings,
        state=state,
        retrieval=retrieval,
        vector_store=vector_store,
        llm=llm,
        model_router=model_router,
        local_fallback_capability=local_fallback_capability,
    )

"""RAG 系统 Knowledge 能力工厂模块。

负责按配置选择本地或远程知识能力 provider，组织 fallback 与 provider 注册。
与主应用的 `app/capabilities/knowledge/factory.py` 功能一致。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.rag_system.config.settings import RagSettings
from app.rag_system.knowledge.base import KnowledgeCapability
from app.rag_system.knowledge.remote import RemoteKnowledgeCapability
from app.rag_system.knowledge.service import RagKnowledgeCapability


class KnowledgeCapabilityProvider(Protocol):
    """描述一个可构建知识能力实例的 provider。"""
    name: str
    def build(self, *, settings: RagSettings, state: Any, retrieval: Any, vector_store: Any, llm: Any | None = None, local_fallback_capability: KnowledgeCapability | None = None) -> KnowledgeCapability: ...


class DefaultKnowledgeCapabilityProvider:
    """默认的本地 Knowledge Capability provider。"""
    name = 'default'
    def build(self, *, settings, state, retrieval, vector_store, llm=None, local_fallback_capability=None) -> KnowledgeCapability:
        return RagKnowledgeCapability(state, retrieval, vector_store, llm)


class RemoteHttpKnowledgeCapabilityProvider:
    """通过远程 HTTP 服务提供 Knowledge Capability。"""
    name = 'remote_http'
    def build(self, *, settings, state, retrieval, vector_store, llm=None, local_fallback_capability=None) -> KnowledgeCapability:
        if not hasattr(settings, 'knowledge_capability_base_url') or not settings.knowledge_capability_base_url:
            raise ValueError('knowledge_capability_base_url is required when provider=remote_http')
        fallback = None
        allow = getattr(settings, 'knowledge_capability_allow_local_fallback', True)
        if allow:
            fallback = local_fallback_capability or DefaultKnowledgeCapabilityProvider().build(
                settings=settings, state=state, retrieval=retrieval, vector_store=vector_store, llm=llm,
            )
        return RemoteKnowledgeCapability(
            base_url=str(settings.knowledge_capability_base_url),
            api_prefix=getattr(settings, 'api_prefix', '/api/v1'),
            timeout_seconds=getattr(settings, 'knowledge_capability_timeout_seconds', 15.0),
            auth_token=getattr(settings, 'knowledge_capability_auth_token', None),
            fallback_capability=fallback,
            allow_local_fallback=allow,
            circuit_breaker_threshold=getattr(settings, 'remote_provider_circuit_breaker_threshold', 3),
            circuit_breaker_cooldown_seconds=getattr(settings, 'remote_provider_circuit_breaker_cooldown_seconds', 30.0),
        )


class KnowledgeCapabilityRegistry:
    """按 provider 名称管理知识能力构建器。"""
    def __init__(self) -> None:
        self._providers: dict[str, KnowledgeCapabilityProvider] = {}

    def register(self, provider: KnowledgeCapabilityProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> KnowledgeCapabilityProvider:
        return self._providers[provider_name]


def build_knowledge_capability(
    *,
    settings: RagSettings,
    state: Any,
    retrieval: Any,
    vector_store: Any,
    llm: Any | None = None,
    provider_name: str = 'default',
    local_fallback_capability: KnowledgeCapability | None = None,
) -> KnowledgeCapability:
    """构建 Knowledge Capability 实例。"""
    registry = KnowledgeCapabilityRegistry()
    registry.register(DefaultKnowledgeCapabilityProvider())
    registry.register(RemoteHttpKnowledgeCapabilityProvider())

    provider = registry.get(provider_name)
    return provider.build(
        settings=settings, state=state, retrieval=retrieval,
        vector_store=vector_store, llm=llm,
        local_fallback_capability=local_fallback_capability,
    )

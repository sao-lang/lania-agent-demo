"""API Contract 能力工厂模块。

负责按 provider 名称构建 API 契约能力实例，把默认本地实现与未来可扩展的
provider 注册逻辑收敛到统一入口。
"""


from __future__ import annotations

from typing import Protocol

from app.capabilities.api_contract.base import ApiContractCapability
from app.capabilities.api_contract.service import build_api_contract_capability
from app.core.config import Settings


class ApiContractCapabilityProvider(Protocol):
    """描述一个可构建 API contract capability 的 provider。"""

    name: str

    def build(self, *, settings: Settings) -> ApiContractCapability:
        """构建当前 provider 对应的 API 契约能力实例。"""

        ...


class DefaultApiContractCapabilityProvider:
    """默认本地 API contract capability provider。"""

    name = 'default'

    def build(self, *, settings: Settings) -> ApiContractCapability:
        """返回默认本地 API 契约能力实现。"""
        return build_api_contract_capability()


class ApiContractCapabilityRegistry:
    """按 provider 名称管理 API contract capability provider。"""

    def __init__(self) -> None:
        """初始化 provider 注册表容器。"""
        self._providers: dict[str, ApiContractCapabilityProvider] = {}

    def register(self, provider: ApiContractCapabilityProvider) -> None:
        """注册一个可按名称检索的 API 契约能力 provider。"""
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> ApiContractCapabilityProvider:
        """按名称返回已注册的 API 契约能力 provider。"""
        return self._providers[provider_name]

    def has(self, provider_name: str) -> bool:
        """判断指定名称的 API 契约能力 provider 是否已注册。"""
        return provider_name in self._providers


def build_default_api_contract_capability_registry() -> ApiContractCapabilityRegistry:
    """构造包含默认本地 provider 的 API 契约能力注册表。"""
    registry = ApiContractCapabilityRegistry()
    registry.register(DefaultApiContractCapabilityProvider())
    return registry


def build_api_contract_capability_from_provider(
    *,
    settings: Settings,
    provider_name: str = 'default',
    registry: ApiContractCapabilityRegistry | None = None,
) -> ApiContractCapability:
    """按给定 provider 名称构建 API 契约能力实例。"""
    effective_registry = registry or build_default_api_contract_capability_registry()
    provider = effective_registry.get(provider_name)
    return provider.build(settings=settings)

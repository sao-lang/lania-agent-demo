"""Artifact 能力工厂模块。

负责管理 artifact capability provider 的注册与选择逻辑，把默认本地实现的
构建过程封装为可替换的工厂入口。
"""


from __future__ import annotations

from typing import Protocol

from app.capabilities.artifact.base import ArtifactCapability
from app.capabilities.artifact.service import LocalArtifactCapability
from app.core.config import Settings
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class ArtifactCapabilityProvider(Protocol):
    """描述一个可构建 artifact capability 的 provider。"""

    name: str

    def build(
        self,
        *,
        settings: Settings,
        state: InMemoryState,
        persistence: SQLiteStateStore | None = None,
    ) -> ArtifactCapability:
        """构建当前 provider 对应的 artifact 能力实例。"""

        ...


class DefaultArtifactCapabilityProvider:
    """默认本地 artifact capability provider。"""

    name = 'default'

    def build(
        self,
        *,
        settings: Settings,
        state: InMemoryState,
        persistence: SQLiteStateStore | None = None,
    ) -> ArtifactCapability:
        """返回默认本地 artifact 能力实现。"""
        return LocalArtifactCapability(state=state, persistence=persistence)


class ArtifactCapabilityRegistry:
    """按 provider 名称管理 artifact capability provider。"""

    def __init__(self) -> None:
        """初始化 artifact provider 注册表。"""
        self._providers: dict[str, ArtifactCapabilityProvider] = {}

    def register(self, provider: ArtifactCapabilityProvider) -> None:
        """注册一个可按名称检索的 artifact provider。"""
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> ArtifactCapabilityProvider:
        """按名称返回已注册的 artifact provider。"""
        return self._providers[provider_name]


def build_default_artifact_capability_registry() -> ArtifactCapabilityRegistry:
    """构造包含默认 provider 的 artifact 能力注册表。"""
    registry = ArtifactCapabilityRegistry()
    registry.register(DefaultArtifactCapabilityProvider())
    return registry


def build_artifact_capability_from_provider(
    *,
    settings: Settings,
    state: InMemoryState,
    persistence: SQLiteStateStore | None = None,
    provider_name: str = 'default',
    registry: ArtifactCapabilityRegistry | None = None,
) -> ArtifactCapability:
    """根据 provider 名称构建 artifact 能力实例。"""
    effective_registry = registry or build_default_artifact_capability_registry()
    provider = effective_registry.get(provider_name)
    return provider.build(settings=settings, state=state, persistence=persistence)

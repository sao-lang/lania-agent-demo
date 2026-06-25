"""数据库能力工厂模块。

负责数据库 capability provider 的注册、选择与实例构建，当前默认装配本地
SQLite 只读能力，并为后续 provider 扩展保留统一入口。
"""


from __future__ import annotations

from typing import Protocol

from app.capabilities.database.base import DatabaseCapability
from app.capabilities.database.service import build_database_capability
from app.core.config import Settings


class DatabaseCapabilityProvider(Protocol):
    """描述一个可构建 Database capability 的 provider。"""

    name: str

    def build(self, *, settings: Settings) -> DatabaseCapability:
        """构建当前 provider 对应的数据库能力实例。"""

        ...


class LocalSQLiteDatabaseCapabilityProvider:
    """默认本地 SQLite Database capability provider。"""

    name = 'sqlite_local'

    def build(self, *, settings: Settings) -> DatabaseCapability:
        """返回默认本地 SQLite 数据库能力实现。"""
        return build_database_capability(settings)


class DatabaseCapabilityRegistry:
    """按 provider 名称管理 Database capability provider。"""

    def __init__(self) -> None:
        """初始化数据库能力 provider 注册表。"""
        self._providers: dict[str, DatabaseCapabilityProvider] = {}

    def register(self, provider: DatabaseCapabilityProvider) -> None:
        """注册一个可按名称检索的数据库能力 provider。"""
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> DatabaseCapabilityProvider:
        """按名称返回已注册的数据库能力 provider。"""
        return self._providers[provider_name]

    def has(self, provider_name: str) -> bool:
        """判断指定名称的数据库能力 provider 是否已注册。"""
        return provider_name in self._providers


def build_default_database_capability_registry() -> DatabaseCapabilityRegistry:
    """构造包含默认 SQLite provider 的数据库能力注册表。"""
    registry = DatabaseCapabilityRegistry()
    registry.register(LocalSQLiteDatabaseCapabilityProvider())
    return registry


def build_database_capability_from_provider(
    *,
    settings: Settings,
    provider_name: str | None = None,
    registry: DatabaseCapabilityRegistry | None = None,
) -> DatabaseCapability:
    """按配置或显式 provider 名称构建数据库能力实例。"""
    effective_registry = registry or build_default_database_capability_registry()
    effective_provider = provider_name or settings.database_capability_provider
    provider = effective_registry.get(effective_provider)
    return provider.build(settings=settings)

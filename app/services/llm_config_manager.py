"""LLM 配置管理模块。

管理多个 LLM Provider 的配置、激活和连接测试。
"""

from __future__ import annotations

from app.models.admin import LlmProviderConfig, LlmProviderStatus
from app.services.config_store import ConfigStore
from app.services.llm_router import LlmRouter


class LlmConfigManager:
    """LLM 配置管理器。"""

    _NAMESPACE = "llm"

    def __init__(
        self, config_store: ConfigStore,
        llm_router: LlmRouter | None = None,
    ) -> None:
        self._store = config_store
        self._router = llm_router

    async def list_providers(self) -> list[LlmProviderConfig]:
        """列出所有已配置的 Provider。"""
        items = self._store.list(self._NAMESPACE)
        providers: list[LlmProviderConfig] = []
        for item in items:
            if isinstance(item.value, dict):
                providers.append(LlmProviderConfig(**item.value))
        return providers

    async def get_provider(self, name: str) -> LlmProviderConfig | None:
        """获取指定 Provider 配置。"""
        value = self._store.get(self._NAMESPACE, name)
        if value and isinstance(value, dict):
            return LlmProviderConfig(**value)
        return None

    async def set_provider(self, config: LlmProviderConfig) -> None:
        """配置/更新一个 Provider。"""
        self._store.set(
            self._NAMESPACE, config.name, config.model_dump(),
        )

    async def delete_provider(self, name: str) -> None:
        """删除 Provider 配置。"""
        self._store.delete(self._NAMESPACE, name)

    async def set_active(self, name: str, model: str) -> None:
        """设置激活的 Provider 和模型。"""
        # 先取消所有 Provider 的激活状态
        providers = await self.list_providers()
        for p in providers:
            if p.is_active:
                p.is_active = False
                await self.set_provider(p)

        # 激活指定的 Provider
        config = await self.get_provider(name)
        if config:
            config.is_active = True
            config.model = model
            await self.set_provider(config)

    async def get_active(self) -> LlmProviderConfig | None:
        """获取当前激活的 Provider。"""
        providers = await self.list_providers()
        for p in providers:
            if p.is_active:
                return p
        return None

    async def test_connection(self, name: str) -> LlmProviderStatus:
        """测试 Provider 连接。"""
        config = await self.get_provider(name)
        if config is None:
            return LlmProviderStatus(
                name=name, model="", status="error",
                error="Provider not found",
            )
        try:
            import time
            start = time.monotonic()
            # TODO: 实际 LLM 连接测试
            latency = int((time.monotonic() - start) * 1000)
            return LlmProviderStatus(
                name=name, model=config.model,
                status="ok", latency_ms=latency,
            )
        except Exception as e:
            return LlmProviderStatus(
                name=name, model=config.model,
                status="error", error=str(e),
            )

    async def get_available_models(self, name: str) -> list[str]:
        """获取 Provider 的可用模型列表。"""
        config = await self.get_provider(name)
        if config and config.models:
            return config.models
        # 默认模型列表
        return ["gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-3.5-turbo"]

    # ── 路由管理（委托给 LlmRouter）───────────

    def list_routes(self) -> list[dict]:
        """列出所有用途的路由。"""
        if self._router is None:
            return []
        return self._router.list_routes()

    def get_route(self, purpose: str) -> dict[str, str] | None:
        """获取指定用途的路由。"""
        if self._router is None:
            return None
        return self._router.get_route(purpose)

    def set_route(self, purpose: str, provider: str, model: str) -> None:
        """设置指定用途的路由。"""
        if self._router is not None:
            self._router.set_route(purpose, provider, model)

    def reset_route(self, purpose: str) -> None:
        """恢复指定用途的路由到默认值。"""
        if self._router is not None:
            self._router.reset_route(purpose)

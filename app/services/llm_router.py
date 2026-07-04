"""LLM 按用途路由模块。

LlmRouter 根据用途（chat, analysis, intent 等）返回对应的 LLM 实例。
不同用途可以配置不同的 provider + model，避免高成本模型处理简单任务。
"""

from __future__ import annotations

import importlib
from typing import Any

from app.core.config import Settings
from app.services.config_store import ConfigStore

# 默认用途路由表
DEFAULT_LLM_ROUTES: dict[str, dict[str, str]] = {
    "chat":       {"provider": "openai", "model": "gpt-4o"},
    "analysis":   {"provider": "openai", "model": "gpt-4o"},
    "intent":     {"provider": "openai", "model": "gpt-4o-mini"},
    "plan":       {"provider": "openai", "model": "gpt-4o-mini"},
    "corrective": {"provider": "openai", "model": "gpt-4o-mini"},
    "extraction": {"provider": "openai", "model": "gpt-4o-mini"},
    "expansion":  {"provider": "openai", "model": "gpt-4o-mini"},
    "eval":       {"provider": "openai", "model": "gpt-4o-mini"},
}

# 用途中文名（用于展示）
PURPOSE_LABELS: dict[str, str] = {
    "chat": "对话回答",
    "analysis": "文档分析",
    "intent": "意图识别",
    "plan": "计划生成",
    "corrective": "Corrective RAG 自检",
    "extraction": "图谱实体抽取",
    "expansion": "多查询扩展",
    "eval": "Ragas 评测",
}


class LlmRouter:
    """LLM 按用途路由器。

    管理用途 → provider + model 的映射，缓存 LLM 实例。
    当配置变更时自动清除缓存，下次调用重建实例。
    """

    _NAMESPACE = "llm_route"

    def __init__(
        self,
        config_store: ConfigStore,
        env_settings: Settings,
    ) -> None:
        self._store = config_store
        self._env = env_settings
        # 用途 → LLM 实例缓存
        self._instances: dict[str, Any] = {}
        # 用途 → 已配置的路由快照（用于检测变更）
        self._route_snapshots: dict[str, dict[str, str]] = {}

    # ── 公开接口 ──────────────────────────────

    def get_llm(self, purpose: str) -> Any | None:
        """获取指定用途的 LLM 实例。

        优先使用运行时配置的 route，没有则用默认路由。
        如果 route 未变更则返回缓存实例。

        Args:
            purpose: 用途名称（chat, analysis, intent 等）。

        Returns:
            LLM 实例，无可用的 LLM 时返回 None。
        """
        route = self._resolve_route(purpose)
        if route is None:
            return None

        # 检查缓存是否有效
        cached = self._instances.get(purpose)
        snapshot = self._route_snapshots.get(purpose)
        if cached is not None and snapshot == route:
            return cached

        # 构建新实例
        instance = self._build_llm(route["provider"], route["model"])
        self._instances[purpose] = instance
        self._route_snapshots[purpose] = route
        return instance

    def get_route(self, purpose: str) -> dict[str, str] | None:
        """获取指定用途的当前路由。"""
        return self._resolve_route(purpose)

    def set_route(self, purpose: str, provider: str, model: str) -> None:
        """设置用途的路由。"""
        self._store.set(self._NAMESPACE, purpose, {
            "provider": provider, "model": model,
        })
        # 清除缓存，下次 get_llm 重建
        self._instances.pop(purpose, None)
        self._route_snapshots.pop(purpose, None)

    def list_routes(self) -> list[dict]:
        """列出所有用途的路由。"""
        purposes = list(DEFAULT_LLM_ROUTES.keys())
        routes: list[dict] = []

        for purpose in purposes:
            route = self._resolve_route(purpose)
            routes.append({
                "purpose": purpose,
                "label": PURPOSE_LABELS.get(purpose, purpose),
                "provider": route["provider"] if route else "N/A",
                "model": route["model"] if route else "N/A",
            })

        return routes

    def reset_route(self, purpose: str) -> None:
        """恢复用途路由到默认值。"""
        self._store.delete(self._NAMESPACE, purpose)
        self._instances.pop(purpose, None)
        self._route_snapshots.pop(purpose, None)

    def invalidate_all(self) -> None:
        """清除所有缓存，下次调用全部重建。"""
        self._instances.clear()
        self._route_snapshots.clear()

    # ── 内部方法 ──────────────────────────────

    def _resolve_route(self, purpose: str) -> dict[str, str] | None:
        """解析用途的最终路由（运行时配置优先于默认）。"""
        # 先查运行时配置
        stored = self._store.get(self._NAMESPACE, purpose)
        if stored and isinstance(stored, dict):
            provider = stored.get("provider")
            model = stored.get("model")
            if provider and model:
                return {"provider": provider, "model": model}

        # 回退到默认
        return DEFAULT_LLM_ROUTES.get(purpose)

    def _build_llm(self, provider: str, model: str) -> Any | None:
        """根据 provider + model 构建 LLM 实例。

        当前支持: openai, anthropic, ollama, custom (OpenAI 兼容)
        未来可扩展。
        """
        provider = provider.lower()

        if provider == "openai":
            return self._build_openai(model)
        elif provider == "anthropic":
            return self._build_anthropic(model)
        elif provider == "ollama":
            return self._build_ollama(model)
        elif provider == "custom":
            return self._build_custom(model)
        else:
            # 未知 provider，回退到 openai
            return self._build_openai(model)

    def _build_openai(self, model: str) -> Any | None:
        """构建 OpenAI LLM 实例。"""
        api_key = self._env.resolved_llm_api_key
        if not api_key:
            return None
        try:
            mod = importlib.import_module("llama_index.llms.openai")
            cls = getattr(mod, "OpenAI")
            kwargs = {
                "model": model,
                "api_key": api_key,
                "timeout": self._env.request_timeout_seconds,
            }
            if self._env.resolved_llm_base_url:
                kwargs["api_base"] = self._env.resolved_llm_base_url
            return cls(**kwargs)
        except ModuleNotFoundError:
            return None

    def _build_anthropic(self, model: str) -> Any | None:
        """构建 Anthropic Claude LLM 实例。"""
        try:
            mod = importlib.import_module("llama_index.llms.anthropic")
            cls = getattr(mod, "Anthropic")
            return cls(model=model)
        except (ModuleNotFoundError, ImportError):
            return None

    def _build_ollama(self, model: str) -> Any | None:
        """构建 Ollama LLM 实例。"""
        try:
            mod = importlib.import_module("llama_index.llms.ollama")
            cls = getattr(mod, "Ollama")
            return cls(model=model, request_timeout=120.0)
        except (ModuleNotFoundError, ImportError):
            return None

    def _build_custom(self, model: str) -> Any | None:
        """构建自定义 OpenAI 兼容 LLM 实例。"""
        api_key = self._env.resolved_llm_api_key or "not-needed"
        base_url = self._env.resolved_llm_base_url
        if not base_url:
            return None
        try:
            mod = importlib.import_module("llama_index.llms.openai")
            cls = getattr(mod, "OpenAI")
            return cls(
                model=model,
                api_key=api_key,
                api_base=base_url,
                timeout=self._env.request_timeout_seconds,
            )
        except ModuleNotFoundError:
            return None

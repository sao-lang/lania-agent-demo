"""系统设置管理模块。

管理运行时系统设置，提供默认值和持久化。
提供 RuntimeConfigReader，组件通过它读取配置，运行时值优先于 env 值。
"""

from __future__ import annotations

from app.core.config import Settings
from app.models.admin import SystemSettings as SystemSettingsModel
from app.services.config_store import ConfigStore


class SystemSettingsManager:
    """系统设置管理器。

    运行时配置的持久化和读取。
    配置存储在 SQLite config_store 表中，namespace='system'。
    """

    _NAMESPACE = "system"
    _KEY = "settings"

    def __init__(self, config_store: ConfigStore) -> None:
        self._store = config_store
        self._defaults = SystemSettingsModel()

    def get_all(self) -> SystemSettingsModel:
        """获取所有设置。"""
        value = self._store.get(self._NAMESPACE, self._KEY)
        if value and isinstance(value, dict):
            return SystemSettingsModel(**value)
        return self._defaults

    def get(self, key: str):
        """获取单个设置。"""
        settings = self.get_all()
        return getattr(settings, key, None)

    def set(self, key: str, value) -> None:
        """修改单个设置。"""
        settings = self.get_all()
        if hasattr(settings, key):
            setattr(settings, key, value)
            self._store.set(
                self._NAMESPACE, self._KEY, settings.model_dump(),
            )


class RuntimeConfigReader:
    """运行时配置读取器。

    包装 env Settings + 运行时 SystemSettings。
    组件通过此对象读取配置，运行时值优先于 env 默认值。
    未在 SystemSettings 中显式设置的值回退到 env Settings。
    """

    def __init__(
        self,
        env_settings: Settings,
        runtime_manager: SystemSettingsManager,
    ) -> None:
        self._env = env_settings
        self._runtime = runtime_manager

    # ── 功能开关 ──────────────────────────────

    @property
    def enable_semantic_cache(self) -> bool:
        rv = self._runtime.get("enable_semantic_cache")
        return rv if rv is not None else self._env.enable_semantic_cache

    @property
    def enable_context_compression(self) -> bool:
        rv = self._runtime.get("enable_context_compression")
        return rv if rv is not None else self._env.enable_context_compression

    @property
    def enable_prompt_guardrails(self) -> bool:
        rv = self._runtime.get("enable_guardrails")
        return rv if rv is not None else self._env.enable_prompt_guardrails

    @property
    def enable_pii_redaction(self) -> bool:
        rv = self._runtime.get("enable_pii_redaction")
        return rv if rv is not None else self._env.enable_pii_redaction

    @property
    def enable_cross_encoder_rerank(self) -> bool:
        rv = self._runtime.get("enable_cross_encoder_rerank")
        return rv if rv is not None else self._env.enable_cross_encoder_rerank

    # ── 语义缓存参数 ──────────────────────────

    @property
    def semantic_cache_similarity_threshold(self) -> float:
        return self._env.semantic_cache_similarity_threshold

    @property
    def semantic_cache_min_query_length(self) -> int:
        return self._env.semantic_cache_min_query_length

    @property
    def semantic_cache_ttl_seconds(self) -> int:
        return self._env.semantic_cache_ttl_seconds

    @property
    def semantic_cache_max_entries_per_collection(self) -> int:
        return self._env.semantic_cache_max_entries_per_collection

    # ── 上下文压缩参数 ────────────────────────

    @property
    def context_compression_max_chunks(self) -> int:
        return self._env.context_compression_max_chunks

    @property
    def context_compression_max_sentences(self) -> int:
        return self._env.context_compression_max_sentences

    @property
    def context_compression_max_chars(self) -> int:
        return self._env.context_compression_max_chars

    # ── Cross-Encoder 参数 ────────────────────

    @property
    def cross_encoder_model(self) -> str:
        return self._env.cross_encoder_model

    @property
    def cross_encoder_device(self) -> str | None:
        return self._env.cross_encoder_device

    # ── 通用 ──────────────────────────────────

    @property
    def default_top_k(self) -> int:
        return self._env.default_top_k

    def get_env(self) -> Settings:
        """获取原始 env Settings 对象。"""
        return self._env

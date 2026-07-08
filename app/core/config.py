"""应用配置模块。

负责集中声明环境变量、默认值和派生属性，供启动流程、RAG 组件、任务系统和持久化层
统一读取。该模块也是理解系统运行参数和功能开关的核心入口。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中管理环境变量和派生配置。

    所有字段默认从环境变量中读取，并允许通过 `Field(..., alias=...)` 维护稳定的
    环境变量名称；同时通过若干 `@property` 暴露运行时常用的派生路径和模型配置。
    API、service、worker 与评测链路都通过这个对象共享运行参数，因此这里既是配置入口，
    也是理解系统功能开关和目录布局的关键位置。
    """

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # 应用基础信息与运行目录。
    # 这一组配置决定服务名称、API 前缀、日志等级以及整个项目的数据根目录。
    app_name: str = Field(default='Personal RAG App', alias='APP_NAME')
    api_prefix: str = Field(default='/api/v1', alias='API_PREFIX')
    app_env: str = Field(default='dev', alias='APP_ENV')
    log_level: str = Field(default='INFO', alias='LOG_LEVEL')
    data_dir: Path = Field(default=Path('/app/data'), alias='DATA_DIR')

    # 模型与回答能力相关配置。
    # 这里集中控制 LLM、Embedding、检索增强、导入清洗与文件转换等所有与 AI 能力直接相关的开关。
    llm_provider: str = Field(default='openai', alias='LLM_PROVIDER')
    llm_model: str = Field(default='gpt-4o-mini', alias='LLM_MODEL')
    llm_api_key: str | None = Field(default=None, alias='LLM_API_KEY')
    llm_base_url: str | None = Field(default=None, alias='LLM_BASE_URL')
    embed_model: str = Field(default='text-embedding-3-small', alias='EMBED_MODEL')
    embed_api_key: str | None = Field(default=None, alias='EMBED_API_KEY')
    embed_base_url: str | None = Field(default=None, alias='EMBED_BASE_URL')
    openai_api_key: str | None = Field(default=None, alias='OPENAI_API_KEY')
    openai_base_url: str | None = Field(default=None, alias='OPENAI_BASE_URL')
    use_local_model_fallback: bool = Field(default=True, alias='USE_LOCAL_MODEL_FALLBACK')
    enable_context_compression: bool = Field(default=True, alias='ENABLE_CONTEXT_COMPRESSION')
    enable_prompt_guardrails: bool = Field(default=True, alias='ENABLE_PROMPT_GUARDRAILS')
    enable_pii_redaction: bool = Field(default=True, alias='ENABLE_PII_REDACTION')
    enable_semantic_cache: bool = Field(default=True, alias='ENABLE_SEMANTIC_CACHE')
    semantic_cache_similarity_threshold: float = Field(default=0.94, alias='SEMANTIC_CACHE_SIMILARITY_THRESHOLD')
    semantic_cache_ttl_seconds: int = Field(default=86400, alias='SEMANTIC_CACHE_TTL_SECONDS')
    semantic_cache_max_entries_per_collection: int = Field(default=500, alias='SEMANTIC_CACHE_MAX_ENTRIES_PER_COLLECTION')
    semantic_cache_min_query_length: int = Field(default=6, alias='SEMANTIC_CACHE_MIN_QUERY_LENGTH')
    context_compression_max_chunks: int = Field(default=4, alias='CONTEXT_COMPRESSION_MAX_CHUNKS')
    context_compression_max_sentences: int = Field(default=8, alias='CONTEXT_COMPRESSION_MAX_SENTENCES')
    context_compression_max_chars: int = Field(default=1600, alias='CONTEXT_COMPRESSION_MAX_CHARS')
    enable_cross_encoder_rerank: bool = Field(default=False, alias='ENABLE_CROSS_ENCODER_RERANK')
    cross_encoder_model: str = Field(default='BAAI/bge-reranker-base', alias='CROSS_ENCODER_MODEL')
    cross_encoder_device: str | None = Field(default=None, alias='CROSS_ENCODER_DEVICE')
    enable_noise_cleanup: bool = Field(default=True, alias='ENABLE_NOISE_CLEANUP')
    enable_metadata_enrichment: bool = Field(default=True, alias='ENABLE_METADATA_ENRICHMENT')
    ingestion_chunking_strategy: str = Field(default='fixed', alias='INGESTION_CHUNKING_STRATEGY')
    semantic_chunk_buffer_size: int = Field(default=1, alias='SEMANTIC_CHUNK_BUFFER_SIZE')
    semantic_chunk_breakpoint_percentile: int = Field(default=95, alias='SEMANTIC_CHUNK_BREAKPOINT_PERCENTILE')
    archive_max_member_count: int = Field(default=200, alias='ARCHIVE_MAX_MEMBER_COUNT')
    archive_max_total_bytes: int = Field(default=100 * 1024 * 1024, alias='ARCHIVE_MAX_TOTAL_BYTES')
    ocr_languages: str = Field(default='eng+chi_sim', alias='OCR_LANGUAGES')
    transcription_model: str = Field(default='base', alias='TRANSCRIPTION_MODEL')
    office_converter_command: str = Field(default='soffice', alias='OFFICE_CONVERTER_COMMAND')
    office_conversion_timeout_seconds: int = Field(default=120, alias='OFFICE_CONVERSION_TIMEOUT_SECONDS')
    converted_cache_max_files: int = Field(default=64, alias='CONVERTED_CACHE_MAX_FILES')
    converted_cache_ttl_seconds: int = Field(default=7 * 24 * 3600, alias='CONVERTED_CACHE_TTL_SECONDS')
    max_import_file_bytes: int = Field(default=50 * 1024 * 1024, alias='MAX_IMPORT_FILE_BYTES')

    # 向量库连接参数。
    # 仅描述 Chroma 的连接与命名空间前缀，不包含业务集合级参数。
    chroma_host: str = Field(default='chroma', alias='CHROMA_HOST')
    chroma_port: int = Field(default=8000, alias='CHROMA_PORT')
    chroma_collection_prefix: str = Field(default='personal-rag', alias='CHROMA_COLLECTION_PREFIX')

    # 检索、编排与后台任务相关参数。
    # 这部分横跨 query workflow、任务 worker、远程 capability/provider 以及策略配置路径。
    default_top_k: int = Field(default=5, alias='DEFAULT_TOP_K')
    default_chunk_size: int = Field(default=800, alias='DEFAULT_CHUNK_SIZE')
    default_chunk_overlap: int = Field(default=100, alias='DEFAULT_CHUNK_OVERLAP')
    request_timeout_seconds: int = Field(default=60, alias='REQUEST_TIMEOUT_SECONDS')
    query_orchestrator: str = Field(default='langgraph', alias='QUERY_ORCHESTRATOR')
    enable_self_rag_retry: bool = Field(default=False, alias='ENABLE_SELF_RAG_RETRY')
    self_rag_max_retry_count: int = Field(default=1, alias='SELF_RAG_MAX_RETRY_COUNT')
    self_rag_min_grounding_confidence: float = Field(default=0.65, alias='SELF_RAG_MIN_GROUNDING_CONFIDENCE')
    enable_query_run_auto_recovery: bool = Field(default=False, alias='ENABLE_QUERY_RUN_AUTO_RECOVERY')
    query_run_auto_recovery_limit: int = Field(default=20, alias='QUERY_RUN_AUTO_RECOVERY_LIMIT')
    enable_embedded_task_worker: bool = Field(default=True, alias='ENABLE_EMBEDDED_TASK_WORKER')
    task_worker_poll_interval_seconds: float = Field(default=1.0, alias='TASK_WORKER_POLL_INTERVAL_SECONDS')
    task_worker_lease_seconds: int = Field(default=1800, alias='TASK_WORKER_LEASE_SECONDS')
    task_worker_max_workers: int = Field(default=1, alias='TASK_WORKER_MAX_WORKERS')
    enable_task_llm_analysis: bool = Field(default=True, alias='ENABLE_TASK_LLM_ANALYSIS')
    enable_task_llm_review: bool = Field(default=True, alias='ENABLE_TASK_LLM_REVIEW')
    # ── 记忆系统 (Phase 6) ─────────────────────────────────────────────────
    memory_max_records_per_task: int = Field(default=200, alias='MEMORY_MAX_RECORDS_PER_TASK')
    memory_enable_semantic: bool = Field(default=True, alias='MEMORY_ENABLE_SEMANTIC')
    memory_enable_profile: bool = Field(default=True, alias='MEMORY_ENABLE_PROFILE')
    memory_auto_promote_interval_minutes: int = Field(default=60, alias='MEMORY_AUTO_PROMOTE_INTERVAL_MINUTES')
    session_max_history: int = Field(default=100, alias='SESSION_MAX_HISTORY')
    database_capability_provider: str = Field(default='sqlite_local', alias='DATABASE_CAPABILITY_PROVIDER')
    knowledge_capability_provider: str = Field(default='default', alias='KNOWLEDGE_CAPABILITY_PROVIDER')
    knowledge_capability_base_url: str | None = Field(default=None, alias='KNOWLEDGE_CAPABILITY_BASE_URL')
    knowledge_capability_timeout_seconds: float = Field(default=15.0, alias='KNOWLEDGE_CAPABILITY_TIMEOUT_SECONDS')
    knowledge_capability_auth_token: str | None = Field(default=None, alias='KNOWLEDGE_CAPABILITY_AUTH_TOKEN')
    knowledge_capability_allow_local_fallback: bool = Field(
        default=True,
        alias='KNOWLEDGE_CAPABILITY_ALLOW_LOCAL_FALLBACK',
    )
    # 外部数据服务 API 密钥。
    # 这部分密钥用于天气、新闻、翻译等外部 API 调用，均非必需——系统会在无密钥时
    # 尝试使用免费/公开接口（如 DuckDuckGo、Nominatim、Frankfurter 等）。
    weather_api_key: str = Field(default='', alias='WEATHER_API_KEY')
    news_api_key: str = Field(default='', alias='NEWS_API_KEY')
    translation_api_key: str = Field(default='', alias='TRANSLATION_API_KEY')

    sandbox_executor_default_policy: str = Field(
        default='sandboxed',
        alias='SANDBOX_EXECUTOR_DEFAULT_POLICY',
    )
    sandbox_executor_provider: str = Field(default='local_process', alias='SANDBOX_EXECUTOR_PROVIDER')
    sandbox_executor_base_url: str | None = Field(default=None, alias='SANDBOX_EXECUTOR_BASE_URL')
    sandbox_executor_timeout_seconds: float = Field(default=15.0, alias='SANDBOX_EXECUTOR_TIMEOUT_SECONDS')
    sandbox_executor_auth_token: str | None = Field(default=None, alias='SANDBOX_EXECUTOR_AUTH_TOKEN')
    sandbox_executor_allow_local_fallback: bool = Field(
        default=True,
        alias='SANDBOX_EXECUTOR_ALLOW_LOCAL_FALLBACK',
    )
    remote_provider_circuit_breaker_threshold: int = Field(
        default=3,
        alias='REMOTE_PROVIDER_CIRCUIT_BREAKER_THRESHOLD',
    )
    remote_provider_circuit_breaker_cooldown_seconds: float = Field(
        default=30.0,
        alias='REMOTE_PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS',
    )
    policy_config_path: Path | None = Field(default=None, alias='POLICY_CONFIG_PATH')

    # Agent 平台相关配置。
    enable_agent_api: bool = Field(default=True, alias='ENABLE_AGENT_API')
    default_agent_mode: str = Field(default='chat', alias='DEFAULT_AGENT_MODE')
    default_capability: str = Field(default='chat', alias='DEFAULT_CAPABILITY')
    enable_mcp_server: bool = Field(default=True, alias='ENABLE_MCP_SERVER')
    mcp_server_path: str = Field(default='/mcp', alias='MCP_SERVER_PATH')

    # 认证相关配置。
    enable_auth: bool = Field(default=False, alias='ENABLE_AUTH')
    auth_default_api_key: str = Field(default='dev-key-123', alias='AUTH_DEFAULT_API_KEY')

    @property
    def resolved_data_dir(self) -> Path:
        """返回实际生效的数据目录。

        Returns:
            在当前运行环境下最终使用的数据目录路径。
        """
        configured = self.data_dir
        # 当容器默认路径在本地环境不可用时，回退到项目目录下的数据目录。
        if configured.is_absolute() and str(configured).startswith('/app') and not configured.parent.exists():
            return Path(__file__).resolve().parents[2] / 'data'
        return configured

    @property
    def uploads_dir(self) -> Path:
        """返回上传文件保存目录。

        Returns:
            上传文件落盘目录路径。
        """
        return self.resolved_data_dir / 'uploads'

    @property
    def eval_dir(self) -> Path:
        """返回评测产物保存目录。

        Returns:
            评测报告与中间结果目录路径。
        """
        return self.resolved_data_dir / 'eval'

    @property
    def document_analysis_baseline_registry_path(self) -> Path:
        """返回文档分析 baseline 注册表文件路径。

        Returns:
            基线注册表 JSON 文件完整路径，供 benchmark/baseline 管理功能复用。
        """
        return self.eval_dir / 'document-analysis-baseline-registry.json'

    @property
    def resolved_policy_config_path(self) -> Path:
        """返回策略配置文件路径。

        Returns:
            显式配置的策略文件路径；未配置时回退到仓库默认的 harness policy 配置。
        """
        if self.policy_config_path is not None:
            return Path(self.policy_config_path).expanduser().resolve()
        return Path(__file__).resolve().parents[2] / 'config' / 'harness-policy-profiles.yaml'

    @property
    def chroma_data_dir(self) -> Path:
        """返回向量库数据目录。

        Returns:
            Chroma 数据目录路径。
        """
        return self.resolved_data_dir / 'chroma'

    @property
    def sqlite_db_path(self) -> Path:
        """返回业务元数据 SQLite 文件路径。

        Returns:
            SQLite 数据库文件完整路径。
        """
        return self.resolved_data_dir / 'app.sqlite3'

    @property
    def resolved_llm_api_key(self) -> str | None:
        """返回 LLM 最终使用的 API Key。

        Returns:
            优先级解析后的 LLM API Key；未配置时返回 `None`。
        """
        return self.llm_api_key or self._provider_api_key(self.llm_provider)

    @property
    def resolved_embed_api_key(self) -> str | None:
        """返回嵌入模型最终使用的 API Key。

        Returns:
            优先级解析后的嵌入模型 API Key；未配置时返回 `None`。
        """
        return self.embed_api_key or self.resolved_llm_api_key or self._provider_api_key(self.llm_provider)

    @property
    def resolved_llm_base_url(self) -> str | None:
        """返回 LLM 最终使用的服务地址。

        Returns:
            优先级解析后的 LLM 服务基地址；未配置时返回 `None`。
        """
        return self.llm_base_url or self._provider_base_url(self.llm_provider)

    @property
    def resolved_embed_base_url(self) -> str | None:
        """返回嵌入模型最终使用的服务地址。

        Returns:
            优先级解析后的嵌入服务基地址；未配置时返回 `None`。
        """
        return self.embed_base_url or self.resolved_llm_base_url or self._provider_base_url(self.llm_provider)

    @property
    def model_api_keys_configured(self) -> bool:
        """判断模型相关密钥是否已经配置。

        Returns:
            只要 LLM 或嵌入模型任一密钥存在，就返回 `True`。
        """
        return bool(self.resolved_llm_api_key or self.resolved_embed_api_key)

    def _provider_api_key(self, provider: str) -> str | None:
        """按提供方读取对应的 API Key。

        Args:
            provider: 模型提供方名称。

        Returns:
            对应提供方的 API Key；当前不支持时返回 `None`。
        """
        normalized = provider.lower()
        if normalized == 'openai':
            return self.openai_api_key
        return None

    def _provider_base_url(self, provider: str) -> str | None:
        """按提供方读取对应的服务地址。

        Args:
            provider: 模型提供方名称。

        Returns:
            对应提供方的服务地址；当前不支持时返回 `None`。
        """
        normalized = provider.lower()
        if normalized == 'openai':
            return self.openai_base_url
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """缓存并返回全局配置实例。

    Returns:
        进程级单例配置对象，避免重复解析环境变量并保证各层读取到一致配置。
    """
    return Settings()

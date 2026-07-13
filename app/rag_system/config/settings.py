"""RAG 系统独立配置模块。

只保留 RAG 需要的配置字段，不包含数据库连接、Redis 等主应用无关字段。
主应用的 ``Settings`` 通过 ``RagSettings.from_app_settings()`` 转换。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    """RAG 系统所需的全部配置。

    所有字段默认从环境变量中读取，并允许通过主应用 Settings 转换生成。
    """

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # Chroma 向量库连接
    chroma_host: str = Field(default='chroma', alias='CHROMA_HOST')
    chroma_port: int = Field(default=8000, alias='CHROMA_PORT')
    chroma_local_path: str = Field(default='./data/chroma', alias='CHROMA_LOCAL_PATH')
    chroma_collection_prefix: str = Field(default='personal-rag', alias='CHROMA_COLLECTION_PREFIX')

    # Embedding 模型
    embed_model: str = Field(default='text-embedding-3-small', alias='EMBED_MODEL')
    embed_api_key: str | None = Field(default=None, alias='EMBED_API_KEY')
    embed_base_url: str | None = Field(default=None, alias='EMBED_BASE_URL')

    # LLM 配置
    llm_provider: str = Field(default='openai', alias='LLM_PROVIDER')
    llm_model: str = Field(default='gpt-4o-mini', alias='LLM_MODEL')
    llm_api_key: str | None = Field(default=None, alias='LLM_API_KEY')
    llm_base_url: str | None = Field(default=None, alias='LLM_BASE_URL')
    openai_api_key: str | None = Field(default=None, alias='OPENAI_API_KEY')
    openai_base_url: str | None = Field(default=None, alias='OPENAI_BASE_URL')

    # 检索参数
    default_top_k: int = Field(default=5, alias='DEFAULT_TOP_K')
    default_chunk_size: int = Field(default=800, alias='DEFAULT_CHUNK_SIZE')
    default_chunk_overlap: int = Field(default=100, alias='DEFAULT_CHUNK_OVERLAP')

    # 语义缓存
    enable_semantic_cache: bool = Field(default=True, alias='ENABLE_SEMANTIC_CACHE')
    semantic_cache_similarity_threshold: float = Field(default=0.94, alias='SEMANTIC_CACHE_SIMILARITY_THRESHOLD')
    semantic_cache_ttl_seconds: int = Field(default=86400, alias='SEMANTIC_CACHE_TTL_SECONDS')
    semantic_cache_max_entries_per_collection: int = Field(default=500, alias='SEMANTIC_CACHE_MAX_ENTRIES_PER_COLLECTION')
    semantic_cache_min_query_length: int = Field(default=6, alias='SEMANTIC_CACHE_MIN_QUERY_LENGTH')

    # 上下文压缩
    enable_context_compression: bool = Field(default=True, alias='ENABLE_CONTEXT_COMPRESSION')
    context_compression_max_chunks: int = Field(default=4, alias='CONTEXT_COMPRESSION_MAX_CHUNKS')
    context_compression_max_sentences: int = Field(default=8, alias='CONTEXT_COMPRESSION_MAX_SENTENCES')
    context_compression_max_chars: int = Field(default=1600, alias='CONTEXT_COMPRESSION_MAX_CHARS')

    # 护栏与脱敏
    enable_prompt_guardrails: bool = Field(default=True, alias='ENABLE_PROMPT_GUARDRAILS')
    enable_pii_redaction: bool = Field(default=True, alias='ENABLE_PII_REDACTION')

    # 重排
    enable_cross_encoder_rerank: bool = Field(default=False, alias='ENABLE_CROSS_ENCODER_RERANK')
    cross_encoder_model: str = Field(default='BAAI/bge-reranker-base', alias='CROSS_ENCODER_MODEL')
    cross_encoder_device: str | None = Field(default=None, alias='CROSS_ENCODER_DEVICE')

    # 导入参数
    enable_noise_cleanup: bool = Field(default=True, alias='ENABLE_NOISE_CLEANUP')
    enable_metadata_enrichment: bool = Field(default=True, alias='ENABLE_METADATA_ENRICHMENT')
    ingestion_chunking_strategy: str = Field(default='fixed', alias='INGESTION_CHUNKING_STRATEGY')
    max_import_file_bytes: int = Field(default=50 * 1024 * 1024, alias='MAX_IMPORT_FILE_BYTES')

    # Self-RAG
    enable_self_rag_retry: bool = Field(default=False, alias='ENABLE_SELF_RAG_RETRY')
    self_rag_max_retry_count: int = Field(default=1, alias='SELF_RAG_MAX_RETRY_COUNT')
    self_rag_min_grounding_confidence: float = Field(default=0.65, alias='SELF_RAG_MIN_GROUNDING_CONFIDENCE')

    # 文件路径
    data_dir: Path = Field(default=Path('/app/data'), alias='DATA_DIR')
    rag_data_path: str = Field(default='rag_data.sqlite3', alias='RAG_DATA_PATH')
    api_prefix: str = Field(default='/api/v1', alias='API_PREFIX')

    # GraphRAG
    use_local_model_fallback: bool = Field(default=True, alias='USE_LOCAL_MODEL_FALLBACK')

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir

    @classmethod
    def from_app_settings(cls, settings) -> RagSettings:
        """从主应用的 Settings 对象转换生成 RagSettings。

        优先读取主 Settings 的 resolved 属性（`resolved_embed_api_key` 等），
        确保 API Key 的 provider 级联解析逻辑在主应用中保持一致。

        Args:
            settings: 主应用的 Settings 实例。

        Returns:
            包含 RAG 相关配置的 RagSettings 实例。
        """
        return cls(
            chroma_host=getattr(settings, 'chroma_host', 'chroma'),
            chroma_port=getattr(settings, 'chroma_port', 8000),
            chroma_local_path=str(getattr(settings, 'resolved_data_dir', Path('./data')) / 'chroma'),
            chroma_collection_prefix=getattr(settings, 'chroma_collection_prefix', 'personal-rag'),
            embed_model=getattr(settings, 'embed_model', 'text-embedding-3-small'),
            embed_api_key=getattr(settings, 'resolved_embed_api_key', None) or getattr(settings, 'embed_api_key', None),
            embed_base_url=getattr(settings, 'resolved_embed_base_url', None) or getattr(settings, 'embed_base_url', None),
            llm_provider=getattr(settings, 'llm_provider', 'openai'),
            llm_model=getattr(settings, 'llm_model', 'gpt-4o-mini'),
            llm_api_key=getattr(settings, 'resolved_llm_api_key', None) or getattr(settings, 'llm_api_key', None),
            llm_base_url=getattr(settings, 'resolved_llm_base_url', None) or getattr(settings, 'llm_base_url', None),
            openai_api_key=getattr(settings, 'openai_api_key', None),
            openai_base_url=getattr(settings, 'openai_base_url', None),
            default_top_k=getattr(settings, 'default_top_k', 5),
            default_chunk_size=getattr(settings, 'default_chunk_size', 800),
            default_chunk_overlap=getattr(settings, 'default_chunk_overlap', 100),
            enable_semantic_cache=getattr(settings, 'enable_semantic_cache', True),
            semantic_cache_similarity_threshold=getattr(settings, 'semantic_cache_similarity_threshold', 0.94),
            semantic_cache_ttl_seconds=getattr(settings, 'semantic_cache_ttl_seconds', 86400),
            semantic_cache_max_entries_per_collection=getattr(settings, 'semantic_cache_max_entries_per_collection', 500),
            semantic_cache_min_query_length=getattr(settings, 'semantic_cache_min_query_length', 6),
            enable_context_compression=getattr(settings, 'enable_context_compression', True),
            context_compression_max_chunks=getattr(settings, 'context_compression_max_chunks', 4),
            context_compression_max_sentences=getattr(settings, 'context_compression_max_sentences', 8),
            context_compression_max_chars=getattr(settings, 'context_compression_max_chars', 1600),
            enable_prompt_guardrails=getattr(settings, 'enable_prompt_guardrails', True),
            enable_pii_redaction=getattr(settings, 'enable_pii_redaction', True),
            enable_cross_encoder_rerank=getattr(settings, 'enable_cross_encoder_rerank', False),
            cross_encoder_model=getattr(settings, 'cross_encoder_model', 'BAAI/bge-reranker-base'),
            cross_encoder_device=getattr(settings, 'cross_encoder_device', None),
            enable_noise_cleanup=getattr(settings, 'enable_noise_cleanup', True),
            enable_metadata_enrichment=getattr(settings, 'enable_metadata_enrichment', True),
            ingestion_chunking_strategy=getattr(settings, 'ingestion_chunking_strategy', 'fixed'),
            max_import_file_bytes=getattr(settings, 'max_import_file_bytes', 50 * 1024 * 1024),
            enable_self_rag_retry=getattr(settings, 'enable_self_rag_retry', False),
            self_rag_max_retry_count=getattr(settings, 'self_rag_max_retry_count', 1),
            self_rag_min_grounding_confidence=getattr(settings, 'self_rag_min_grounding_confidence', 0.65),
            data_dir=getattr(settings, 'data_dir', Path('/app/data')),
            rag_data_path=getattr(settings, 'rag_data_path', 'rag_data.sqlite3'),
            api_prefix=getattr(settings, 'api_prefix', '/api/v1'),
            use_local_model_fallback=getattr(settings, 'use_local_model_fallback', True),
        )

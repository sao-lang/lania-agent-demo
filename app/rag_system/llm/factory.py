"""RAG 系统 LLM 构建模块。

负责根据配置构建 LLM 和 Embedding 模型实例。
"""

from __future__ import annotations

from typing import Any

from app.rag_system.config.settings import RagSettings
from app.rag_system.vector_store.llamaindex_adapter import build_embed_model, build_llm


def build_rag_llm(settings: RagSettings) -> Any | None:
    """构建 RAG 系统使用的 LLM 实例。

    Args:
        settings: RAG 系统配置。

    Returns:
        LLM 实例，未配置时返回 None。
    """
    return build_llm(settings)


def build_rag_embed_model(settings: RagSettings) -> Any:
    """构建 RAG 系统使用的 Embedding 模型实例。

    Args:
        settings: RAG 系统配置。

    Returns:
        Embedding 模型实例。
    """
    return build_embed_model(settings)

"""LlamaIndex 适配器模块。

负责构建 Embedding、LLM、Chroma 向量存储适配器和元数据过滤器转换逻辑。
与主应用的 `app/rag/llamaindex_components.py` 功能一致，但使用独立配置。
"""

from __future__ import annotations

import importlib
import math
import re
from typing import Any

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.vector_stores import FilterCondition, FilterOperator, MetadataFilter, MetadataFilters

from app.rag_system.config.settings import RagSettings
from app.rag_system.vector_store.chroma import ChromaClientFactory


class HashEmbedding(BaseEmbedding):
    """在没有外部 Embedding 服务时提供本地哈希向量兜底。"""

    model_name: str = 'local-hash'
    embed_dim: int = 256

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._hash_embed(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._hash_embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> list[float]:
        tokens = re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower())
        if not tokens:
            return [0.0] * self.embed_dim
        vector = [0.0] * self.embed_dim
        for token in tokens:
            index = hash(token) % self.embed_dim
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


def build_embed_model(settings: RagSettings) -> BaseEmbedding:
    """优先构建远程 Embedding 模型，失败时回退到本地哈希模型。"""
    if settings.embed_api_key:
        try:
            openai_embeddings = importlib.import_module('llama_index.embeddings.openai')
            OpenAIEmbedding = getattr(openai_embeddings, 'OpenAIEmbedding')
            kwargs: dict[str, Any] = {
                'model': settings.embed_model,
                'api_key': settings.embed_api_key,
            }
            if settings.embed_base_url:
                kwargs['api_base'] = settings.embed_base_url
            return OpenAIEmbedding(**kwargs)
        except ModuleNotFoundError:
            pass
    return HashEmbedding(embed_dim=256)


def build_llm(settings: RagSettings) -> Any | None:
    """根据配置构建 LLM；没配密钥时直接返回 None。"""
    api_key = settings.llm_api_key or settings.openai_api_key
    if not api_key:
        return None
    try:
        openai_llm = importlib.import_module('llama_index.llms.openai')
        OpenAILLM = getattr(openai_llm, 'OpenAI')
        kwargs: dict[str, Any] = {
            'model': settings.llm_model,
            'api_key': api_key,
        }
        if settings.llm_base_url or settings.openai_base_url:
            kwargs['api_base'] = settings.llm_base_url or settings.openai_base_url
        return OpenAILLM(**kwargs)
    except ModuleNotFoundError:
        return None


def build_vector_store(vector_store_factory: ChromaClientFactory, collection_name: str) -> Any:
    """构建 LlamaIndex 可用的 ChromaVectorStore 实例。

    延迟导入 ChromaVectorStore 以避免缺失依赖导致启动失败。
    """
    from llama_index.vector_stores.chroma import ChromaVectorStore
    chroma_collection = vector_store_factory.get_or_create_collection(collection_name)
    return ChromaVectorStore(chroma_collection=chroma_collection)


def build_metadata_filters(filters: dict[str, Any] | None) -> MetadataFilters | None:
    """将简单过滤条件字典转换为 LlamaIndex 的 MetadataFilters 对象。

    Args:
        filters: 简单过滤条件，如 {"field": "value"} 或 {"field": {"$gt": 0.5}}。

    Returns:
        可供 LlamaIndex 使用的 MetadataFilters 实例。
    """
    if not filters:
        return None

    filter_list = []
    for key, value in filters.items():
        if isinstance(value, dict):
            for op, op_value in value.items():
                operator = _operator_mapping(op)
                filter_list.append(MetadataFilter(key=key, operator=operator, value=op_value))
        else:
            filter_list.append(MetadataFilter(key=key, operator=FilterOperator.EQ, value=value))

    return MetadataFilters(filters=filter_list, condition=FilterCondition.AND)


def _operator_mapping(op: str) -> FilterOperator:
    mapping = {
        '$gt': FilterOperator.GT,
        '$gte': FilterOperator.GTE,
        '$lt': FilterOperator.LT,
        '$lte': FilterOperator.LTE,
        '$eq': FilterOperator.EQ,
        '$ne': FilterOperator.NE,
        '$in': FilterOperator.TEXT_IN,
        '$nin': FilterOperator.TEXT_NOT_IN,
    }
    return mapping.get(op, FilterOperator.EQ)

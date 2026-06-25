"""LlamaIndex 组件构建模块。

负责统一构建 Embedding、LLM、Chroma 向量存储适配器和元数据过滤器转换逻辑，
免得这些基础组件的构建细节散在检索、摄取和问答链路的各个角落。
"""

from __future__ import annotations

import importlib
import math
import re
from typing import Any

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.vector_stores import FilterCondition, FilterOperator, MetadataFilter, MetadataFilters
from llama_index.vector_stores.chroma import ChromaVectorStore

from app.core.config import Settings
from app.rag.vector_store import ChromaClientFactory
from app.types import MetadataFilters as MetadataFiltersMap


class HashEmbedding(BaseEmbedding):
    """在没有外部 Embedding 服务时提供本地哈希向量兜底。"""

    model_name: str = 'local-hash'
    embed_dim: int = 256

    def _get_query_embedding(self, query: str) -> list[float]:
        """生成查询文本的向量表示。"""
        return self._hash_embed(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        """异步生成查询文本的向量表示。"""
        return self._hash_embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        """生成文档文本的向量表示。"""
        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> list[float]:
        """基于 token 哈希构造归一化稀疏向量。

        Args:
            text: 待向量化文本。

        Returns:
            固定维度的归一化向量。
        """
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


def build_embed_model(settings: Settings) -> BaseEmbedding:
    """优先构建远程 Embedding 模型，失败时回退到本地哈希模型。

    Args:
        settings: 全局配置对象。

    Returns:
        可供 LlamaIndex 使用的嵌入模型实例。
    """
    if settings.resolved_embed_api_key:
        try:
            openai_embeddings = importlib.import_module('llama_index.embeddings.openai')
            OpenAIEmbedding = getattr(openai_embeddings, 'OpenAIEmbedding')
            kwargs: dict[str, Any] = {
                'model': settings.embed_model,
                'api_key': settings.resolved_embed_api_key,
            }
            if settings.resolved_embed_base_url:
                kwargs['api_base'] = settings.resolved_embed_base_url
            return OpenAIEmbedding(**kwargs)
        except ModuleNotFoundError:
            pass

    # 远程 Embedding 不可用时，至少保证检索链路还能继续跑。
    return HashEmbedding(embed_dim=256)


def build_llm(settings: Settings) -> Any | None:
    """根据配置构建 LLM；没配密钥时直接返回 `None`。

    Args:
        settings: 全局配置对象。

    Returns:
        成功构建时返回 LLM 实例，否则返回 `None`。
    """
    if not settings.resolved_llm_api_key:
        return None

    try:
        openai_llms = importlib.import_module('llama_index.llms.openai')
        OpenAI = getattr(openai_llms, 'OpenAI')
        kwargs: dict[str, Any] = {
            'model': settings.llm_model,
            'api_key': settings.resolved_llm_api_key,
            'timeout': settings.request_timeout_seconds,
        }
        if settings.resolved_llm_base_url:
            kwargs['api_base'] = settings.resolved_llm_base_url
        return OpenAI(**kwargs)
    except ModuleNotFoundError:
        return None


def build_vector_store(factory: ChromaClientFactory, collection_name: str) -> ChromaVectorStore:
    """基于 Chroma collection 构建 LlamaIndex 向量存储适配器。

    Args:
        factory: Chroma 客户端工厂与访问封装。
        collection_name: 目标知识库名称。

    Returns:
        绑定指定 collection 的 LlamaIndex 向量存储实例。
    """
    chroma_collection = factory.get_or_create_collection(collection_name)
    return ChromaVectorStore(chroma_collection=chroma_collection)


def build_metadata_filters(filters: MetadataFiltersMap | None) -> MetadataFilters | None:
    """把接口层过滤条件转换成 LlamaIndex 元数据过滤器。

    Args:
        filters: API 层传入的元数据过滤条件字典。

    Returns:
        LlamaIndex 可识别的过滤器对象；无可下推条件时返回 `None`。
    """
    if not filters:
        return None

    filter_items: list[MetadataFilter | MetadataFilters] = []
    for key, value in filters.items():
        if key == 'tags' or key.endswith('tags'):
            # tags 过滤在业务层按包含关系处理，这里不直接往向量库下推。
            continue
        metadata_filter = _build_single_metadata_filter(key, value)
        if metadata_filter is not None:
            filter_items.append(metadata_filter)

    if not filter_items:
        return None
    return MetadataFilters(filters=filter_items, condition=FilterCondition.AND)


def _build_single_metadata_filter(
    key: str, value: Any
) -> MetadataFilter | MetadataFilters | None:
    """把单个接口过滤项转换成向量库可下推的过滤条件。

    Args:
        key: 过滤字段名。
        value: 字段对应的过滤值或操作字典。

    Returns:
        单个或组合过滤条件；无法转换时返回 `None`。
    """
    if key == 'year':
        return _build_numeric_metadata_filter('year_int', value)
    if key == 'quarter':
        return _build_numeric_metadata_filter('quarter_num', value, normalizer=_normalize_quarter_number)
    if key in {'permission', 'version'}:
        return _build_text_metadata_filter(key, value, normalizer=_normalize_permission_value if key == 'permission' else None)

    if isinstance(value, dict):
        expected = value.get('eq')
        if expected is None:
            return None
        return MetadataFilter(key=key, value=str(expected), operator=FilterOperator.EQ)
    if isinstance(value, list):
        normalized = [str(item) for item in value if item is not None]
        if not normalized:
            return None
        return MetadataFilter(key=key, value=normalized, operator=FilterOperator.IN)
    return MetadataFilter(key=key, value=str(value), operator=FilterOperator.EQ)


def _build_numeric_metadata_filter(
    key: str,
    value: Any,
    normalizer: Any = None,
) -> MetadataFilter | MetadataFilters | None:
    """构建数值型 metadata filter，支持 `eq` / `in` / `gte` / `lte`。

    Args:
        key: 过滤字段名。
        value: 过滤值或范围条件字典。
        normalizer: 可选标准化函数，用于把输入值转换为目标数值。

    Returns:
        构造完成的数值过滤条件；无法转换时返回 `None`。
    """
    normalize = normalizer or _normalize_int_value
    if isinstance(value, dict):
        range_filters: list[MetadataFilter | MetadataFilters] = []
        if value.get('eq') is not None:
            eq_value = normalize(value.get('eq'))
            if eq_value is None:
                return None
            range_filters.append(MetadataFilter(key=key, value=eq_value, operator=FilterOperator.EQ))
        if value.get('in') is not None:
            in_values = [normalize(item) for item in value.get('in', [])]
            normalized_in = [item for item in in_values if item is not None]
            if normalized_in:
                range_filters.append(MetadataFilter(key=key, value=normalized_in, operator=FilterOperator.IN))
        if value.get('gte') is not None:
            gte_value = normalize(value.get('gte'))
            if gte_value is not None:
                range_filters.append(MetadataFilter(key=key, value=gte_value, operator=FilterOperator.GTE))
        if value.get('lte') is not None:
            lte_value = normalize(value.get('lte'))
            if lte_value is not None:
                range_filters.append(MetadataFilter(key=key, value=lte_value, operator=FilterOperator.LTE))
        if not range_filters:
            return None
        if len(range_filters) == 1:
            return range_filters[0]
        return MetadataFilters(filters=range_filters, condition=FilterCondition.AND)
    if isinstance(value, list):
        normalized = [normalize(item) for item in value]
        normalized_values = [item for item in normalized if item is not None]
        if not normalized_values:
            return None
        return MetadataFilter(key=key, value=normalized_values, operator=FilterOperator.IN)
    normalized_value = normalize(value)
    if normalized_value is None:
        return None
    return MetadataFilter(key=key, value=normalized_value, operator=FilterOperator.EQ)


def _build_text_metadata_filter(
    key: str,
    value: Any,
    normalizer: Any = None,
) -> MetadataFilter | None:
    """构建字符串型 metadata filter，支持 `eq` / `in`。

    Args:
        key: 过滤字段名。
        value: 过滤值或操作字典。
        normalizer: 可选标准化函数。

    Returns:
        构造完成的文本过滤条件；无法转换时返回 `None`。
    """
    normalize = normalizer or _normalize_text_value
    if isinstance(value, dict):
        if value.get('eq') is not None:
            normalized_eq = normalize(value.get('eq'))
            if normalized_eq is not None:
                return MetadataFilter(key=key, value=normalized_eq, operator=FilterOperator.EQ)
        if value.get('in') is not None:
            normalized_in = [normalize(item) for item in value.get('in', [])]
            values = [item for item in normalized_in if item is not None]
            if values:
                return MetadataFilter(key=key, value=values, operator=FilterOperator.IN)
        return None
    if isinstance(value, list):
        normalized_in = [normalize(item) for item in value]
        values = [item for item in normalized_in if item is not None]
        if not values:
            return None
        return MetadataFilter(key=key, value=values, operator=FilterOperator.IN)
    normalized_value = normalize(value)
    if normalized_value is None:
        return None
    return MetadataFilter(key=key, value=normalized_value, operator=FilterOperator.EQ)


def _normalize_int_value(value: Any) -> int | None:
    """把输入标准化成整数。

    Args:
        value: 待标准化值。

    Returns:
        转换成功时返回整数，否则返回 `None`。
    """
    if value in (None, ''):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_quarter_number(value: Any) -> int | None:
    """把季度值标准化成 1-4。

    Args:
        value: 待标准化季度值，例如 `Q1`、`1`。

    Returns:
        转换成功时返回季度数字，否则返回 `None`。
    """
    if value in (None, ''):
        return None
    text = str(value).upper().strip()
    if text.startswith('Q'):
        text = text[1:]
    try:
        quarter = int(text)
    except (TypeError, ValueError):
        return None
    return quarter if quarter in {1, 2, 3, 4} else None


def _normalize_text_value(value: Any) -> str | None:
    """把输入标准化成文本。

    Args:
        value: 待标准化值。

    Returns:
        去除首尾空白后的文本；为空时返回 `None`。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_permission_value(value: Any) -> str | None:
    """把权限过滤值标准化成统一枚举。

    Args:
        value: 权限过滤原始值。

    Returns:
        统一后的权限枚举字符串；无法识别时保留其小写文本。
    """
    text = _normalize_text_value(value)
    if text is None:
        return None
    alias_map = {
        'public': 'public',
        'open': 'public',
        '公开': 'public',
        'internal': 'internal',
        'intranet': 'internal',
        '内部': 'internal',
        'private': 'private',
        '私有': 'private',
        'restricted': 'restricted',
        'sensitive': 'restricted',
        '受限': 'restricted',
        '敏感': 'restricted',
        'confidential': 'confidential',
        'secret': 'confidential',
        '机密': 'confidential',
        '保密': 'confidential',
    }
    return alias_map.get(text.lower(), text.lower())

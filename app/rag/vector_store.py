"""Chroma 向量库访问模块。

负责统一管理 Chroma 客户端连接、collection 访问、向量写入、删除和查询逻辑，并在
远程 Chroma 不可用时自动回退到本地持久化实例，保证系统在开发环境下仍可运行。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, cast

import chromadb
from chromadb.api.types import Embedding, Metadata, QueryResult

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ChromaClientFactory:
    """Chroma 客户端工厂和访问封装。

    这里把“连远端、远端不通时回退本地、统一 collection 前缀和基础 CRUD”这些事情都包起来，
    上层就不用关心连接细节了。
    """

    def __init__(self, settings: Settings) -> None:
        """保存 Chroma 连接配置。

        Args:
            settings: 全局配置对象，提供 Chroma 连接信息和本地回退目录。
        """
        self.settings = settings
        self._client: Any | None = None

    def ping(self) -> str:
        """通过 heartbeat 探测向量库可用性。

        Returns:
            正常可用时返回 `up`，发生异常时返回 `degraded`。
        """
        try:
            client = self.get_client()
            client.heartbeat()
            return 'up'
        except Exception as exc:
            logger.warning('Chroma heartbeat failed: %s', exc)
            return 'degraded'

    def get_client(self) -> Any:
        """优先连远程 Chroma，失败时回退到本地持久化实例。

        Returns:
            可直接执行 collection 操作的 Chroma 客户端实例。
        """
        if self._client is not None:
            return self._client

        try:
            client = chromadb.HttpClient(host=self.settings.chroma_host, port=self.settings.chroma_port)
            client.heartbeat()
            self._client = client
            return client
        except Exception as exc:
            # 远程 Chroma 不可达时，自动切换到本地目录模式保证系统可继续运行。
            logger.warning('Falling back to local Chroma persistent client: %s', exc)
            self.settings.chroma_data_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.settings.chroma_data_dir))
            return self._client

    def get_or_create_collection(self, name: str) -> Any:
        """获取或创建带统一前缀的 collection。

        Args:
            name: 业务层传入的逻辑知识库名称。

        Returns:
            带系统前缀的 Chroma collection 对象。
        """
        client = self.get_client()
        return client.get_or_create_collection(name=self._collection_name(name))

    def delete_collection(self, name: str) -> None:
        """删除指定 collection；不存在就直接忽略。

        Args:
            name: 业务层传入的逻辑知识库名称。
        """
        client = self.get_client()
        try:
            client.delete_collection(name=self._collection_name(name))
        except Exception as exc:
            logger.info('Chroma collection delete skipped for %s: %s', name, exc)

    def upsert_chunks(self, collection_name: str, chunks: list[dict[str, Any]]) -> int:
        """批量写入文本分块和元数据。

        Args:
            collection_name: 目标知识库名称。
            chunks: 待写入分块列表，每项需包含 `chunk_id`、`text` 和 `metadata`。

        Returns:
            实际写入的分块数量。
        """
        if not chunks:
            return 0

        collection = self.get_or_create_collection(collection_name)
        ids = [chunk['chunk_id'] for chunk in chunks]
        documents = [chunk['text'] for chunk in chunks]
        embeddings = cast(list[Embedding], [self.embed_text(text) for text in documents])
        metadatas = cast(list[Metadata], [self._normalize_metadata(chunk['metadata']) for chunk in chunks])
        collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        return len(ids)

    def delete_chunks(self, collection_name: str, chunk_ids: list[str]) -> None:
        """按分块 ID 删除向量数据。

        Args:
            collection_name: 目标知识库名称。
            chunk_ids: 需要删除的分块 ID 列表。
        """
        if not chunk_ids:
            return

        collection = self.get_or_create_collection(collection_name)
        collection.delete(ids=chunk_ids)

    def query(self, collection_name: str, question: str, top_k: int) -> QueryResult:
        """直接对 collection 执行向量查询。

        Args:
            collection_name: 目标知识库名称。
            question: 查询文本。
            top_k: 返回结果数量上限。

        Returns:
            Chroma 原始查询结果。
        """
        collection = self.get_or_create_collection(collection_name)
        query_embedding = self.embed_text(question)
        return collection.query(
            query_embeddings=[query_embedding],
            n_results=max(top_k, 1),
            include=['documents', 'metadatas', 'distances'],
        )

    def embed_text(self, text: str, dimensions: int = 256) -> list[float]:
        """用简单哈希向量给文本生成嵌入表示。

        Args:
            text: 待向量化文本。
            dimensions: 向量维度。

        Returns:
            固定维度的归一化向量。
        """
        tokens = re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower())
        if not tokens:
            return [0.0] * dimensions

        vector = [0.0] * dimensions
        for token in tokens:
            index = hash(token) % dimensions
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _collection_name(self, name: str) -> str:
        """拼接系统约定的 collection 名前缀。

        Args:
            name: 业务层逻辑知识库名称。

        Returns:
            实际用于 Chroma 的 collection 名称。
        """
        return f"{self.settings.chroma_collection_prefix}-{name}"

    def _normalize_metadata(self, metadata: dict[str, Any]) -> Metadata:
        """把复杂元数据转成 Chroma 能接受的标量结构。

        Args:
            metadata: 原始分块元数据字典。

        Returns:
            Chroma 可写入的标量元数据结构。
        """
        normalized: dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, list):
                # 列表型元数据序列化为管道分隔字符串，便于后续检索过滤。
                normalized[key] = '|'.join(str(item) for item in value)
            else:
                normalized[key] = value
        return cast(Metadata, normalized)

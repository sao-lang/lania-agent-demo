"""Chroma 向量库访问模块。

负责独立管理 Chroma 客户端连接、collection 访问、向量写入、删除和查询逻辑，
与主应用的 ``ChromaClientFactory`` 功能一致但使用独立配置。
"""

from __future__ import annotations

import logging
from typing import Any

import chromadb

from app.rag_system.config.settings import RagSettings

logger = logging.getLogger(__name__)


class ChromaClientFactory:
    """Chroma 客户端工厂和访问封装。

    优先连远程 Chroma，失败时回退到本地持久化实例。
    """

    def __init__(self, settings: RagSettings) -> None:
        """保存 Chroma 连接配置。

        Args:
            settings: RAG 系统配置，提供 Chroma 连接信息和本地回退目录。
        """
        self.settings = settings
        self._client: Any | None = None

    def ping(self) -> str:
        """通过 heartbeat 探测向量库可用性。"""
        try:
            client = self.get_client()
            client.heartbeat()
            return 'up'
        except Exception as exc:
            logger.warning('Chroma heartbeat failed: %s', exc)
            return 'degraded'

    def get_client(self) -> Any:
        """优先连远程 Chroma，失败时回退到本地持久化实例。"""
        if self._client is not None:
            return self._client

        try:
            client = chromadb.HttpClient(host=self.settings.chroma_host, port=self.settings.chroma_port)
            client.heartbeat()
            self._client = client
            return client
        except Exception as exc:
            logger.warning('Falling back to local Chroma persistent client: %s', exc)
            local_path = self.settings.chroma_local_path
            Path(local_path).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=local_path)
            return self._client

    def get_or_create_collection(self, name: str) -> Any:
        """获取或创建带统一前缀的 collection。"""
        client = self.get_client()
        return client.get_or_create_collection(name=self._collection_name(name))

    def delete_collection(self, name: str) -> None:
        """删除指定 collection。"""
        client = self.get_client()
        try:
            client.delete_collection(name=self._collection_name(name))
        except Exception as exc:
            logger.info('Chroma collection delete skipped for %s: %s', name, exc)

    def _collection_name(self, name: str) -> str:
        """为 collection 添加统一前缀。"""
        prefix = self.settings.chroma_collection_prefix
        return f'{prefix}_{name}' if prefix else name

    def upsert_chunks(self, collection_name: str, chunks: list[dict[str, Any]]) -> int:
        """批量写入文本分块和元数据。

        Args:
            collection_name: 逻辑知识库名称。
            chunks: 包含 ids、embeddings、documents、metadatas 的分块列表。

        Returns:
            成功写入的分块数量。
        """
        collection = self.get_or_create_collection(collection_name)
        ids = [c['id'] for c in chunks]
        embeddings = [c.get('embedding') for c in chunks]
        documents = [c.get('text', '') for c in chunks]
        metadatas = [c.get('metadata', {}) for c in chunks]
        # 过滤掉 None embedding 的分块
        valid: list[tuple[str, list[float] | None, str, dict[str, Any]]] = []
        for idx, cid in enumerate(ids):
            emb = embeddings[idx] if idx < len(embeddings) else None
            valid.append((cid, emb, documents[idx] if idx < len(documents) else '', metadatas[idx] if idx < len(metadatas) else {}))
        batch_ids = [v[0] for v in valid]
        batch_embeddings: list[list[float]] | None = [v[1] for v in valid if v[1] is not None] or None
        batch_documents = [v[2] for v in valid]
        batch_metadatas = [v[3] for v in valid]

        kwargs: dict[str, Any] = {'ids': batch_ids, 'documents': batch_documents, 'metadatas': batch_metadatas}
        if batch_embeddings:
            kwargs['embeddings'] = batch_embeddings

        collection.upsert(**kwargs)
        return len(valid)

    def query_chunks(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """执行向量相似度查询。

        Args:
            collection_name: 逻辑知识库名称。
            query_embedding: 查询向量。
            top_k: 返回结果数量。
            where: 可选的元数据过滤条件。

        Returns:
            查询结果列表，每条包含 id、text、metadata 和 score。
        """
        collection = self.get_or_create_collection(collection_name)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=['documents', 'metadatas', 'distances'],
        )
        if not results['ids']:
            return []

        items = []
        for idx, cid in enumerate(results['ids'][0]):
            items.append({
                'id': cid,
                'text': results['documents'][0][idx] if results['documents'] else '',
                'metadata': results['metadatas'][0][idx] if results['metadatas'] else {},
                'score': 1.0 - results['distances'][0][idx] if results['distances'] else 0.0,
            })
        return items

    def list_collections(self) -> list[str]:
        """列出所有带前缀的 collection 名称。"""
        client = self.get_client()
        prefix = self.settings.chroma_collection_prefix
        collections = client.list_collections()
        names = [c.name for c in collections]
        if prefix:
            stripped = []
            for n in names:
                if n.startswith(prefix + '_'):
                    stripped.append(n[len(prefix) + 1:])
            return stripped
        return names

    def delete_chunks(self, collection_name: str, chunk_ids: list[str]) -> None:
        """删除指定分块。"""
        collection = self.get_or_create_collection(collection_name)
        collection.delete(ids=chunk_ids)

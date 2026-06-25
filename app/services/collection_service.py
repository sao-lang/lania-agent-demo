"""集合服务模块。

负责管理知识库集合的生命周期，包括创建、查询、删除，以及在删除时协调向量库、
图谱索引、本地上传目录和语义缓存的一致性清理。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.models.collection import CollectionCreateRequest, CollectionSummary
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.semantic_cache import SemanticCacheService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import CollectionRecord


class CollectionService:
    """管理集合元数据，并协调向量库中的集合生命周期。"""

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        vector_store: ChromaClientFactory,
        persistence: SQLiteStateStore | None = None,
        semantic_cache: SemanticCacheService | None = None,
        graph_service: GraphService | None = None,
    ) -> None:
        """保存集合服务所需的配置、状态和向量库依赖。

        Args:
            settings: 全局配置对象。
            state: 内存态业务数据。
            vector_store: 向量库访问封装。
            persistence: 可选持久化存储。
            semantic_cache: 可选语义缓存服务，用于集合变更时清理缓存。
            graph_service: 可选图谱服务，用于集合删除时清理图结构。
        """
        self.settings = settings
        self.state = state
        self.vector_store = vector_store
        self.persistence = persistence
        self.semantic_cache = semantic_cache
        self.graph_service = graph_service

    def create(self, payload: CollectionCreateRequest) -> CollectionSummary:
        """创建集合；若同名集合已存在则直接返回其摘要。

        Args:
            payload: 集合创建请求。

        Returns:
            新建或已存在集合的摘要信息。
        """
        existing = self.state.collections.get(payload.name)
        if existing is not None:
            return self._build_summary(existing)

        now = datetime.now(timezone.utc)
        record: CollectionRecord = {
            'id': f'col-{uuid4().hex[:8]}',
            'name': payload.name,
            'description': payload.description,
            'status': 'created',
            'embedding_model': payload.embedding_model,
            'chunk_size': payload.chunk_size,
            'chunk_overlap': payload.chunk_overlap,
            'created_at': now,
            'updated_at': now,
        }
        self.state.collections[payload.name] = record
        if self.persistence is not None:
            self.persistence.upsert_collection(record)
        # 在内存状态登记后，立即保证向量库中的底层 collection 可用。
        self.vector_store.get_or_create_collection(payload.name)
        return self._build_summary(record)

    def list_all(self) -> list[CollectionSummary]:
        """返回当前所有集合的摘要信息。"""
        return [self._build_summary(item) for item in self.state.collections.values()]

    def delete(self, collection_name: str) -> bool:
        """删除集合及其关联文档和本地上传目录。

        Args:
            collection_name: 目标集合名称。

        Returns:
            删除成功返回 `True`，集合不存在返回 `False`。
        """
        record = self.state.collections.pop(collection_name, None)
        if record is None:
            return False

        # 先清理与集合绑定的派生数据，再删除底层 collection 和本地目录。
        if self.semantic_cache is not None:
            self.semantic_cache.invalidate_collection(collection_name, reason='collection_deleted')
        if self.graph_service is not None:
            self.graph_service.delete_collection_graph(collection_name)
        if self.persistence is not None:
            self.persistence.delete_collection(collection_name)
        self.vector_store.delete_collection(collection_name)
        collection_dir = self.settings.uploads_dir / collection_name
        self._delete_directory(collection_dir)

        # 集合删除后，内存中属于该集合的文档记录也要一并清理。
        to_delete = [doc_id for doc_id, doc in self.state.documents.items() if doc['collection_name'] == collection_name]
        for doc_id in to_delete:
            self.state.documents.pop(doc_id, None)
            if self.persistence is not None:
                self.persistence.delete_document(doc_id)
        return True

    def get(self, collection_name: str) -> CollectionSummary | None:
        """获取单个集合的摘要信息。

        Args:
            collection_name: 目标集合名称。

        Returns:
            集合存在时返回摘要，否则返回 `None`。
        """
        record = self.state.collections.get(collection_name)
        if record is None:
            return None
        return self._build_summary(record)

    def _build_summary(self, record: CollectionRecord) -> CollectionSummary:
        """根据集合记录和文档统计信息构建响应对象。

        Args:
            record: 集合记录。

        Returns:
            包含文档数量和分块统计的集合摘要对象。
        """
        collection_name = record['name']
        documents = [doc for doc in self.state.documents.values() if doc['collection_name'] == collection_name]
        indexed_chunks = sum(doc.get('indexed_chunks', 0) for doc in documents)
        summary = CollectionSummary.model_validate(record)
        return summary.model_copy(
            update={
                'document_count': len(documents),
                'indexed_chunks': indexed_chunks,
            }
        )

    def _delete_directory(self, path: Path) -> None:
        """递归删除集合对应的本地目录。

        Args:
            path: 待删除目录路径。
        """
        if not path.exists():
            return

        for child in path.iterdir():
            if child.is_dir():
                self._delete_directory(child)
            else:
                child.unlink()
        path.rmdir()

"""文档服务模块。

负责承接文档上传、目录扫描、重建索引和删除等文档生命周期操作，并协调摄取服务、
语义缓存和图谱索引，保证文档变更后检索链路状态保持一致。
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from app.core.config import Settings
from app.core.errors import bad_request_error, not_found_error
from app.models.document import (
    DocumentItem,
    DocumentUploadResponse,
    ImportFailureItem,
    ImportStats,
    ReindexRequest,
    ScanRequest,
)
from app.rag.ingestion import RagIngestionService
from app.services.graph_service import GraphService
from app.services.semantic_cache import SemanticCacheService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class DocumentService:
    """负责文档生命周期管理，并协调底层索引构建。"""

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        ingestion_service: RagIngestionService,
        persistence: SQLiteStateStore | None = None,
        semantic_cache: SemanticCacheService | None = None,
        graph_service: GraphService | None = None,
    ) -> None:
        """保存服务依赖并确保基础数据目录已经存在。

        Args:
            settings: 全局配置对象。
            state: 内存态业务数据。
            ingestion_service: 文档摄取服务。
            persistence: 可选持久化存储。
            semantic_cache: 可选语义缓存服务。
            graph_service: 可选图谱服务。
        """
        self.settings = settings
        self.state = state
        self.ingestion_service = ingestion_service
        self.persistence = persistence
        self.semantic_cache = semantic_cache
        self.graph_service = graph_service
        self.ingestion_service.ensure_data_dirs()

    async def upload(self, collection_name: str, tags: list[str], files: list[UploadFile]) -> DocumentUploadResponse:
        """上传一批文件到指定集合，并在上传后立即尝试建立索引。

        Args:
            collection_name: 目标集合名称。
            tags: 需要附加到文档上的标签列表。
            files: 待上传文件列表。

        Returns:
            包含成功上传项、失败项和统计信息的响应对象。
        """
        if collection_name not in self.state.collections:
            raise not_found_error('collection', collection_name)
        if self.semantic_cache is not None:
            self.semantic_cache.invalidate_collection(collection_name, reason='documents_uploaded')

        destination_dir = self.settings.uploads_dir / collection_name
        destination_dir.mkdir(parents=True, exist_ok=True)

        uploaded: list[DocumentItem] = []
        failed: list[ImportFailureItem] = []
        stats = {
            'input_files': len(files),
            'imported_documents': 0,
            'failed_files': 0,
            'skipped_files': 0,
        }

        for upload in files:
            try:
                content = await upload.read()
                file_name = upload.filename or 'upload.txt'
                file_type = self.ingestion_service.detect_file_type(Path(file_name))
                validation_error = self.ingestion_service.validate_import_candidate(
                    file_name,
                    file_type=file_type,
                    file_size=len(content),
                    content_type=upload.content_type,
                )
                if validation_error is not None:
                    failed.append(ImportFailureItem.model_validate(validation_error))
                    stats['failed_files'] += 1
                    continue
                # 先落到临时目录再走统一导入链路，避免上传入口和目录扫描入口逻辑分叉。
                with tempfile.TemporaryDirectory(prefix='rag-upload-') as tmp_dir_name:
                    source_path = Path(tmp_dir_name) / file_name
                    source_path.write_bytes(content)
                    records = self.ingestion_service.import_path(source_path, collection_name, tags)
                    uploaded.extend(DocumentItem.model_validate(record) for record in records)
                    stats['imported_documents'] += len(records)
            except Exception as exc:
                failed.append(
                    ImportFailureItem.model_validate(
                        self.ingestion_service.build_import_failure(
                            upload.filename or 'unknown',
                            exc,
                            file_type=self.ingestion_service.detect_file_type(Path(upload.filename or 'unknown')),
                        )
                    )
                )
                stats['failed_files'] += 1

        return DocumentUploadResponse(
            uploaded=uploaded,
            failed=failed,
            stats=ImportStats.model_validate(stats),
        )

    def list_documents(self, collection_name: str | None = None) -> list[DocumentItem]:
        """按更新时间倒序返回文档列表。

        Args:
            collection_name: 可选集合名称；传入后仅返回该集合下文档。

        Returns:
            文档响应对象列表。
        """
        documents = list(self.state.documents.values())
        if collection_name is not None:
            documents = [item for item in documents if item['collection_name'] == collection_name]
        ordered = sorted(
            documents,
            key=lambda item: item.get('updated_at') or item.get('created_at') or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [DocumentItem.model_validate(item) for item in ordered]

    def scan(self, payload: ScanRequest) -> dict[str, object]:
        """扫描目录并批量导入满足条件的文件。

        Args:
            payload: 目录扫描请求。

        Returns:
            扫描与导入结果字典。
        """
        if payload.collection_name not in self.state.collections:
            raise not_found_error('collection', payload.collection_name)
        if self.semantic_cache is not None:
            self.semantic_cache.invalidate_collection(payload.collection_name, reason='documents_scanned')
        result = self.ingestion_service.scan_directory(
            directory=payload.directory,
            collection_name=payload.collection_name,
            recursive=payload.recursive,
            file_types=payload.file_types,
        )
        # 把 ingestion 层的失败结果统一转成标准业务异常，避免 API 层感知内部返回结构。
        if result.get('status') == 'failed':
            raise bad_request_error(
                code='scan_failed',
                message=str(result.get('reason') or 'scan failed'),
                details={
                    'collection_name': payload.collection_name,
                    'directory': payload.directory,
                },
            )
        return result

    def reindex(self, payload: ReindexRequest) -> dict[str, object]:
        """按文档列表或集合范围重新建立索引。

        Args:
            payload: 重建索引请求。

        Returns:
            重建索引的执行结果字典。
        """
        if payload.doc_ids:
            collection_names = {
                self.state.documents[doc_id]['collection_name']
                for doc_id in payload.doc_ids
                if doc_id in self.state.documents
            }
            if self.semantic_cache is not None:
                for collection_name in sorted(collection_names):
                    self.semantic_cache.invalidate_collection(collection_name, reason='documents_reindexed')
            # 显式指定 doc_ids 时，只重建目标文档，避免集合级重建带来不必要的开销。
            return self.ingestion_service.reindex_documents(payload.doc_ids)

        if payload.collection_name:
            if payload.collection_name not in self.state.collections:
                raise not_found_error('collection', payload.collection_name)
            if self.semantic_cache is not None:
                self.semantic_cache.invalidate_collection(payload.collection_name, reason='collection_reindexed')
            doc_ids = [doc_id for doc_id, doc in self.state.documents.items() if doc['collection_name'] == payload.collection_name]
            return self.ingestion_service.reindex_documents(doc_ids)

        raise bad_request_error(
            code='reindex_target_required',
            message='collection_name 或 doc_ids 至少提供一个',
        )

    def delete(self, doc_id: str) -> bool:
        """删除文档对应的源文件和向量分块。

        Args:
            doc_id: 目标文档 ID。

        Returns:
            删除成功返回 `True`，文档不存在返回 `False`。
        """
        record = self.state.documents.get(doc_id)
        if record is None:
            return False

        # 文档删除会改变可检索证据集合，因此先失效该集合缓存，再清理索引和源文件。
        if self.semantic_cache is not None:
            self.semantic_cache.invalidate_collection(record['collection_name'], reason='document_deleted')
        if self.graph_service is not None:
            self.graph_service.delete_document_graph(doc_id)
        self.ingestion_service.delete_document_chunks(doc_id)
        target = Path(record['file_path'])
        if target.exists():
            target.unlink()
        self.state.documents.pop(doc_id, None)
        if self.persistence is not None:
            self.persistence.delete_document(doc_id)
        return True

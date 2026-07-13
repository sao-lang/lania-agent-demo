"""RAG 系统文档摄取主入口模块。

负责把多种文档格式转换为可检索的向量分块。
与主应用的 `app/rag/ingestion.py` 功能一致，但使用独立配置和状态。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.rag_system.config.settings import RagSettings
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.retrieval.graph_service import RagGraphService
from app.rag_system.store.persistence import RagPersistence
from app.rag_system.store.state import RagState
from app.rag_system.vector_store.chroma import ChromaClientFactory
from app.rag_system.vector_store.llamaindex_adapter import build_embed_model


from app.rag_system.ingestion.parts.pdf_layout import RagIngestionPdfLayoutMixin
from app.rag_system.ingestion.parts.pdf_segments import RagIngestionPdfSegmentsMixin
from app.rag_system.ingestion.parts.extractors import RagIngestionExtractorMixin
from app.rag_system.ingestion.parts.segment_processing import RagIngestionSegmentProcessingMixin
from app.rag_system.ingestion.parts.document_builders import RagIngestionDocumentBuilderMixin
from app.rag_system.ingestion.parts.imports import RagIngestionImportMixin


class RagIngestionService(
    RagIngestionPdfLayoutMixin,
    RagIngestionPdfSegmentsMixin,
    RagIngestionExtractorMixin,
    RagIngestionSegmentProcessingMixin,
    RagIngestionDocumentBuilderMixin,
    RagIngestionImportMixin,
):
    """负责把多种文档格式转换为可检索的向量分块。"""

    TABLE_SEGMENT_ROW_BATCH = 40
    TEXT_LIKE_TYPES = {
        'txt', 'md', 'rst', 'log', 'json', 'jsonl', 'yaml', 'yml',
        'toml', 'ini', 'cfg', 'conf', 'xml', 'sql', 'csv', 'tsv',
        'py', 'js', 'jsx', 'ts', 'tsx', 'java', 'go', 'rs',
        'c', 'cc', 'cpp', 'h', 'hpp', 'cs', 'rb', 'php', 'swift', 'kt',
        'sh', 'bash', 'zsh', 'ps1', 'bat', 'cmd',
        'html', 'htm', 'css', 'scss', 'less',
        'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
        'tex', 'bib', 'r', 'm', 'scala', 'clj', 'lua', 'pl', 'pm',
        'dockerfile', 'makefile', 'gradle', 'cmake',
    }
    IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'webp', 'tiff', 'ico'}
    AUDIO_TYPES = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'wma', 'm4a'}
    VIDEO_TYPES = {'mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm'}
    OFFICE_TYPES = {
        'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'odt', 'ods', 'odp', 'rtf',
        'pdf', 'epub', 'mobi',
    }

    def __init__(
        self,
        settings: RagSettings,
        state: RagState,
        vector_store: ChromaClientFactory,
        trace: TraceRecorder,
        persistence: RagPersistence | None = None,
        graph_service: RagGraphService | None = None,
    ) -> None:
        """初始化文档摄取服务。

        Args:
            settings: RAG 系统配置。
            state: RAG 系统内存状态。
            vector_store: 向量库访问封装。
            trace: 链路追踪记录器。
            persistence: 可选持久化存储。
            graph_service: 可选的图谱服务。
        """
        self.settings = settings
        self.state = state
        self.vector_store = vector_store
        self.trace = trace
        self.persistence = persistence
        self.graph_service = graph_service
        self.embed_model = build_embed_model(settings)

    def ingest_file(
        self,
        collection_name: str,
        file_path: str | Path,
        doc_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """摄取单个文件。

        Args:
            collection_name: 目标知识库名称。
            file_path: 文件路径。
            doc_id: 可选文档 ID。
            metadata: 可选的元数据。

        Returns:
            摄取结果统计。
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        doc_id = doc_id or str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        file_ext = path.suffix.lstrip('.').lower()

        # 读取文件内容
        content = self._read_file_content(path, file_ext)

        # 分段
        segments = self._chunk_text(content, doc_id)

        # 生成嵌入并写入向量库
        chunk_ids = self._index_segments(collection_name, segments, doc_id, metadata or {})

        # 更新状态
        doc_record = {
            'doc_id': doc_id,
            'collection_name': collection_name,
            'file_name': path.name,
            'file_type': file_ext,
            'document_title': path.stem,
            'indexed_chunks': len(chunk_ids),
            'document_hierarchy': {},
            'created_at': now,
            'metadata': metadata or {},
        }
        self.state.documents[doc_id] = doc_record
        if self.persistence:
            self.persistence.upsert_document(doc_record)

        # 图谱抽取（如启用）
        graph_result = None
        if self.graph_service:
            graph_result = self.graph_service.extract_and_store(collection_name, doc_id, segments)

        result = {
            'doc_id': doc_id,
            'file_name': path.name,
            'chunks': len(chunk_ids),
            'collection_name': collection_name,
            'graph': graph_result,
        }
        self.trace.record('rag_ingested', result)
        return result

    def delete_document(self, collection_name: str, doc_id: str) -> bool:
        """删除文档及其向量分块。"""
        doc = self.state.documents.pop(doc_id, None)
        if doc is None:
            return False

        # 从向量库删除 chunk
        collection = self.vector_store.get_or_create_collection(collection_name)
        try:
            # 获取该文档的所有 chunk
            all_data = collection.get(
                where={'doc_id': doc_id},
                include=['metadatas'],
            )
            if all_data and all_data['ids']:
                collection.delete(ids=all_data['ids'])
        except Exception:
            pass

        if self.persistence:
            self.persistence.delete_document(doc_id)

        # 删除图谱数据
        if self.graph_service:
            self.graph_service.delete_document_graph(doc_id)

        return True

    def _read_file_content(self, path: Path, file_ext: str) -> str:
        """读取文件内容。"""
        if file_ext in self.TEXT_LIKE_TYPES:
            return path.read_text(encoding='utf-8', errors='replace')
        else:
            # 非文本文件尝试读取
            try:
                return path.read_text(encoding='utf-8', errors='replace')
            except Exception:
                return f"[二进制文件: {path.name}]"

    def _chunk_text(self, text: str, doc_id: str) -> list[dict[str, Any]]:
        """将文本分段。"""
        chunk_size = self.settings.default_chunk_size
        chunk_overlap = self.settings.default_chunk_overlap

        paragraphs = re.split(r'\n\s*\n', text)
        segments: list[dict[str, Any]] = []
        current_chunk = ''
        seq = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                seq += 1
                segments.append({
                    'text': current_chunk.strip(),
                    'seq': seq,
                    'doc_id': doc_id,
                })
                # 保留重叠部分
                overlap_start = max(0, len(current_chunk) - chunk_overlap)
                current_chunk = current_chunk[overlap_start:] + '\n' + para
            else:
                if current_chunk:
                    current_chunk += '\n' + para
                else:
                    current_chunk = para

        if current_chunk.strip():
            seq += 1
            segments.append({
                'text': current_chunk.strip(),
                'seq': seq,
                'doc_id': doc_id,
            })

        return segments

    def _index_segments(
        self,
        collection_name: str,
        segments: list[dict[str, Any]],
        doc_id: str,
        metadata: dict[str, Any],
    ) -> list[str]:
        """将分段写入向量库。"""
        chunks: list[dict[str, Any]] = []
        for seg in segments:
            chunk_id = f"{doc_id}-chunk-{seg['seq']:04d}"
            text = seg['text']

            # 计算嵌入
            try:
                embedding = self.embed_model.get_text_embedding(text)
            except Exception:
                embedding = None

            chunk_meta = {
                'doc_id': doc_id,
                'seq': seg['seq'],
                'file_name': metadata.get('file_name', ''),
                'file_type': metadata.get('file_type', ''),
                **metadata,
            }

            chunks.append({
                'id': chunk_id,
                'text': text,
                'embedding': embedding,
                'metadata': chunk_meta,
            })

        self.vector_store.upsert_chunks(collection_name, chunks)
        return [c['id'] for c in chunks]

    def import_files(
        self,
        collection_name: str,
        file_paths: list[str | Path],
    ) -> list[dict[str, Any]]:
        """批量导入文件。"""
        results = []
        for fp in file_paths:
            try:
                result = self.ingest_file(collection_name, fp)
                results.append(result)
            except Exception as exc:
                results.append({'error': str(exc), 'file': str(fp)})
        return results

    def ensure_data_dirs(self) -> None:
        """确保数据目录存在。"""
        self.settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        upload_dir = self.settings.resolved_data_dir / 'uploads'
        upload_dir.mkdir(parents=True, exist_ok=True)

    def ingest_document(self, doc_id: str, force: bool = False) -> dict[str, Any]:
        """按 doc_id 重新摄取已有文档。

        Args:
            doc_id: 文档 ID。
            force: 是否强制重新摄取。

        Returns:
            摄取结果统计。
        """
        doc = self.state.documents.get(doc_id)
        if doc is None:
            raise ValueError(f'文档不存在: {doc_id}')
        if doc.get('indexed_chunks', 0) > 0 and not force:
            return {'doc_id': doc_id, 'status': 'skipped', 'reason': 'already_indexed'}

        collection_name = doc.get('collection_name', '')
        file_name = doc.get('file_name', '')
        file_path = self.settings.resolved_data_dir / 'uploads' / file_name

        if not file_path.exists():
            raise FileNotFoundError(f'源文件不存在: {file_path}')

        return self.ingest_file(collection_name, file_path, doc_id=doc_id, metadata={
            'file_name': file_name,
            'file_type': doc.get('file_type', file_name.rsplit('.', 1)[-1].lower()),
        })

    def reindex_documents(self, collection_name: str | None = None) -> list[dict[str, Any]]:
        """重建指定集合（或全部）文档的索引。

        Args:
            collection_name: 可选的知识库名称，为空时重建全部。

        Returns:
            每个文档的摄取结果列表。
        """
        results = []
        for doc_id, doc in list(self.state.documents.items()):
            if collection_name and doc.get('collection_name') != collection_name:
                continue
            # 先删除旧 chunks
            self.delete_document_chunks(doc.get('collection_name', ''), doc_id)
            try:
                result = self.ingest_document(doc_id, force=True)
                results.append(result)
            except Exception as exc:
                results.append({'doc_id': doc_id, 'status': 'error', 'error': str(exc)})
        return results

    def scan_directory(self, dir_path: str | Path, collection_name: str, recursive: bool = True) -> list[dict[str, Any]]:
        """扫描目录并导入所有支持的文件。

        Args:
            dir_path: 目录路径。
            collection_name: 目标知识库名称。
            recursive: 是否递归扫描子目录。

        Returns:
            每个文件的导入结果列表。
        """
        path = Path(dir_path)
        if not path.is_dir():
            raise NotADirectoryError(f'不是目录: {dir_path}')

        results = []
        supported_exts = self.TEXT_LIKE_TYPES | self.OFFICE_TYPES
        pattern = '**/*' if recursive else '*'
        for fp in sorted(path.glob(pattern)):
            if not fp.is_file():
                continue
            ext = fp.suffix.lstrip('.').lower()
            if ext not in supported_exts:
                continue
            try:
                result = self.ingest_file(collection_name, fp)
                results.append(result)
            except Exception as exc:
                results.append({'error': str(exc), 'file': str(fp)})
        return results

    def delete_document_chunks(self, collection_name: str, doc_id: str) -> int:
        """从向量库中删除文档的所有分块。

        Args:
            collection_name: 知识库名称。
            doc_id: 文档 ID。

        Returns:
            删除的分块数量。
        """
        try:
            collection = self.vector_store.get_or_create_collection(collection_name)
            all_data = collection.get(where={'doc_id': doc_id}, include=['metadatas'])
            if all_data and all_data['ids']:
                collection.delete(ids=all_data['ids'])
                return len(all_data['ids'])
        except Exception:
            pass
        return 0

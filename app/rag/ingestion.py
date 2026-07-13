"""文档摄取主入口。

该模块负责组织文档导入、内容提取、分段规整、向量切块、索引写入和状态更新流程。
更复杂的格式解析、分段规整、索引文档构建与导入辅助逻辑已经拆到 `ingestion_parts`
下的子模块中，这样调用方仍然只依赖 `RagIngestionService`，而维护者可以按职责快速定位代码。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from uuid import uuid4


from app.core.config import Settings
from app.rag.llamaindex_components import build_embed_model
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import DocumentRecord

from app.rag.ingestion_parts.pdf_layout import IngestionPdfLayoutMixin
from app.rag.ingestion_parts.pdf_segments import IngestionPdfSegmentsMixin
from app.rag.ingestion_parts.extractors import IngestionExtractorMixin
from app.rag.ingestion_parts.segment_processing import IngestionSegmentProcessingMixin
from app.rag.ingestion_parts.document_builders import IngestionDocumentBuilderMixin
from app.rag.ingestion_parts.imports import IngestionImportMixin


class _HTMLTextExtractor(HTMLParser):
    """把 HTML 内容提取为纯文本片段。"""

    def __init__(self) -> None:
        """初始化纯文本片段缓冲区。"""
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """收集 HTML 标签之间的可见文本。

        Args:
            data: 当前解析到的文本片段。
        """
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        """返回拼接后的纯文本内容。

        Returns:
            以换行拼接后的纯文本字符串。
        """
        return "\n".join(self.parts)


class RagIngestionService(
    IngestionPdfLayoutMixin,
    IngestionPdfSegmentsMixin,
    IngestionExtractorMixin,
    IngestionSegmentProcessingMixin,
    IngestionDocumentBuilderMixin,
    IngestionImportMixin,
):
    """负责把多种文档格式转换为可检索的向量分块。"""

    TABLE_SEGMENT_ROW_BATCH = 40
    TEXT_LIKE_TYPES = {
        'txt',
        'md',
        'rst',
        'log',
        'json',
        'jsonl',
        'yaml',
        'yml',
        'toml',
        'ini',
        'cfg',
        'conf',
        'xml',
        'sql',
        'py',
        'js',
        'jsx',
        'ts',
        'tsx',
        'java',
        'go',
        'rs',
        'c',
        'cc',
        'cpp',
        'h',
        'hpp',
        'cs',
        'rb',
        'php',
        'swift',
        'kt',
        'scala',
        'sh',
        'bash',
        'zsh',
        'fish',
        'ps1',
    }
    TEXT_LIKE_NAMES = {'dockerfile', 'makefile', 'justfile', 'readme', 'license'}
    IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'tif', 'tiff'}
    AUDIO_TYPES = {'mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg'}
    VIDEO_TYPES = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v'}
    OFFICE_TYPES = {'doc', 'docx', 'ppt', 'pptx', 'csv', 'xlsx'}
    MIME_OVERRIDES = {
        'md': {'text/markdown', 'text/x-markdown'},
        'txt': {'text/plain'},
        'html': {'text/html'},
        'htm': {'text/html'},
        'pdf': {'application/pdf'},
        'doc': {'application/msword'},
        'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
        'ppt': {'application/vnd.ms-powerpoint'},
        'pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
        'csv': {'text/csv', 'application/csv', 'application/vnd.ms-excel'},
        'xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        'zip': {'application/zip', 'application/x-zip-compressed', 'multipart/x-zip'},
        'png': {'image/png'},
        'jpg': {'image/jpeg'},
        'jpeg': {'image/jpeg'},
        'gif': {'image/gif'},
        'bmp': {'image/bmp'},
        'webp': {'image/webp'},
        'tif': {'image/tiff'},
        'tiff': {'image/tiff'},
        'mp3': {'audio/mpeg'},
        'wav': {'audio/wav', 'audio/x-wav'},
        'm4a': {'audio/mp4', 'audio/x-m4a'},
        'aac': {'audio/aac'},
        'flac': {'audio/flac'},
        'ogg': {'audio/ogg'},
        'mp4': {'video/mp4'},
        'mov': {'video/quicktime'},
        'avi': {'video/x-msvideo'},
        'mkv': {'video/x-matroska'},
        'webm': {'video/webm'},
    }
    SUPPORTED_TYPES = {
        'pdf',
        'html',
        'htm',
        'zip',
        *TEXT_LIKE_TYPES,
        *TEXT_LIKE_NAMES,
        *IMAGE_TYPES,
        *AUDIO_TYPES,
        *VIDEO_TYPES,
        *OFFICE_TYPES,
    }
    NOISE_LINE_PATTERNS = (
        re.compile(r'^\s*page\s+\d+\s+of\s+\d+\s*$', re.IGNORECASE),
        re.compile(r'^\s*第\s*\d+\s*页\s*/\s*共\s*\d+\s*页\s*$'),
        re.compile(r'^\s*(版权所有|版权声明|免责声明)\b.*$'),
        re.compile(r'^\s*all rights reserved\.?\s*$', re.IGNORECASE),
        re.compile(r'^\s*confidential\s*$', re.IGNORECASE),
    )
    KEYWORD_STOPWORDS = {
        'the',
        'and',
        'for',
        'with',
        'from',
        'that',
        'this',
        'are',
        'was',
        'were',
        'have',
        'has',
        'had',
        'into',
        'onto',
        'your',
        'you',
        'our',
        'their',
        'them',
        '它们',
        '以及',
        '如果',
        '因为',
        '所以',
        '然后',
        '或者',
        '相关',
        '进行',
        '用于',
        '通过',
        '一个',
        '一种',
        '可以',
        '需要',
        '支持',
        '当前',
        '已经',
        '我们',
        '你们',
        '他们',
        '其中',
        '这些',
        '那些',
        '说明',
        '文档',
        '章节',
        '内容',
        '信息',
        '系统',
        '功能',
        '模块',
        '接口',
        '步骤',
    }
    SEMANTIC_FIXED_BLOCK_ROLES = {'table_like', 'figure_caption', 'figure', 'heading'}
    SEMANTIC_FIXED_MEDIA_KINDS = {'image', 'pdf_page_image'}

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        vector_store: ChromaClientFactory,
        trace: TraceRecorder,
        persistence: SQLiteStateStore | None = None,
        graph_service: GraphService | None = None,
    ) -> None:
        """初始化摄取服务及其相关依赖。

        Args:
            settings: 全局配置对象，决定导入限制、切块策略和目录路径。
            state: 内存态业务数据，用于读取与更新文档记录。
            vector_store: 向量库访问封装，用于写入和删除文档分块。
            trace: 链路追踪记录器，用于输出摄取阶段观测事件。
            persistence: 可选的持久化存储，用于把文档状态落盘。
            graph_service: 可选的图谱服务，用于同步构建 GraphRAG 文档结构。
        """
        self.settings = settings
        self.state = state
        self.vector_store = vector_store
        self.trace = trace
        self.persistence = persistence
        self.graph_service = graph_service
        self.embed_model = build_embed_model(settings)
        self._converted_cache_prune_runs = 0
        self._converted_cache_deleted_files = 0
        self._converted_cache_last_pruned_at: datetime | None = None

    def ensure_data_dirs(self) -> None:
        """确保上传、评测和向量数据目录存在。

        该方法在导入前统一准备运行目录，避免不同入口重复判断目录存在性。
        """
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.settings.eval_dir.mkdir(parents=True, exist_ok=True)
        self.settings.chroma_data_dir.mkdir(parents=True, exist_ok=True)
        self._converted_cache_dir().mkdir(parents=True, exist_ok=True)

    def ingest_document(self, doc_id: str, force: bool = False) -> dict[str, Any]:
        """读取单个文档、切分为节点并写入向量库。

        Args:
            doc_id: 待处理文档的唯一标识。
            force: 是否强制重建索引；为 `False` 时已索引文档会被跳过。

        Returns:
            包含文档 ID、处理状态和索引结果统计的字典。
        """
        self.ensure_data_dirs()
        record = self.state.documents.get(doc_id)
        if record is None:
            return {'doc_id': doc_id, 'status': 'failed', 'reason': 'document not found'}

        if not force and record.get('indexed_chunks'):
            # 已建立索引时优先短路，避免重复导入覆盖原来的向量块和元数据统计。
            return {
                'doc_id': doc_id,
                'status': 'skipped',
                'indexed_chunks': record.get('indexed_chunks', 0),
                'reason': 'already indexed',
            }

        file_path = Path(record['file_path'])
        if not file_path.exists():
            record['status'] = 'failed'
            self._persist_document(record)
            return {'doc_id': doc_id, 'status': 'failed', 'reason': 'file not found'}

        try:
            raw_segments = self._extract_segments(file_path)
            segments, doc_metadata = self._prepare_segments(record, raw_segments)
            self.trace.record(
                'semantic_chunking_prepared',
                self._build_semantic_chunking_trace_payload(
                    collection_name=record['collection_name'],
                    raw_segments=raw_segments,
                    prepared_segments=segments,
                ),
            )
            record['document_title'] = doc_metadata['document_title']
            record['document_summary'] = doc_metadata['document_summary']
            record['document_keywords'] = doc_metadata['document_keywords']
            record['year'] = doc_metadata['year']
            record['quarter'] = doc_metadata['quarter']
            record['version'] = doc_metadata['version']
            record['permission'] = doc_metadata['permission']
            record['document_hierarchy'] = doc_metadata['document_hierarchy']
            if doc_metadata.get('source_archive'):
                record['source_archive'] = doc_metadata['source_archive']
            if doc_metadata.get('archive_member_path'):
                record['archive_member_path'] = doc_metadata['archive_member_path']
            if doc_metadata.get('archive_member_display_path'):
                record['archive_member_display_path'] = doc_metadata['archive_member_display_path']
            if self.graph_service is not None:
                try:
                    # 图谱与向量索引共用同一批规整后的 segments，保证两条检索链路的边界一致。
                    self.graph_service.replace_document_graph(record, segments)
                except Exception as exc:
                    self.trace.record(
                        'graph_document_index_failed',
                        {
                            'doc_id': doc_id,
                            'collection_name': record['collection_name'],
                            'reason': str(exc),
                        },
                    )
            documents = self._build_documents(record, segments)
            previous_chunk_ids = record.get('chunk_ids', [])
            # 强制重建时先清掉旧分块，避免历史索引残留。
            if previous_chunk_ids:
                self.vector_store.delete_chunks(record['collection_name'], previous_chunk_ids)

            # 只有在旧索引清理完成后才重新跑 pipeline，避免新旧 chunk 混杂。
            pipeline = self._build_pipeline(record['collection_name'])
            nodes = pipeline.run(documents=documents)
            chunk_ids = [node.node_id for node in nodes]
            inserted = len(nodes)
            now = datetime.now(timezone.utc)
            record['status'] = 'indexed'
            record['indexed_chunks'] = inserted
            record['chunk_ids'] = chunk_ids
            record['indexed_at'] = now
            record['updated_at'] = now
            self._persist_document(record)
            self.trace.record(
                'document_indexed',
                {
                    'doc_id': doc_id,
                    'collection_name': record['collection_name'],
                    'chunk_count': inserted,
                    'chunking_strategy': self._resolve_chunking_strategy(record['collection_name']),
                    'year': record.get('year'),
                    'quarter': record.get('quarter'),
                    'version': record.get('version'),
                    'permission': record.get('permission'),
                },
            )
            return {'doc_id': doc_id, 'status': 'indexed', 'indexed_chunks': inserted}
        except Exception as exc:
            # 摄取阶段任一步失败都落回文档记录，便于后续在文档列表和重试入口中定位原因。
            record['status'] = 'failed'
            record['error'] = str(exc)
            record['updated_at'] = datetime.now(timezone.utc)
            self._persist_document(record)
            self.trace.record('document_index_failed', {'doc_id': doc_id, 'reason': str(exc)})
            return {'doc_id': doc_id, 'status': 'failed', 'reason': str(exc)}

    def _build_semantic_chunking_trace_payload(
        self,
        *,
        collection_name: str,
        raw_segments: list[dict[str, Any]],
        prepared_segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """构造语义切块预处理阶段的观测指标。

        Args:
            collection_name: 当前文档所属知识库名称。
            raw_segments: 原始提取出的片段列表。
            prepared_segments: 经过规整、合并后的片段列表。

        Returns:
            适合写入追踪系统的语义切块统计信息。
        """
        requested_strategy = self._resolve_chunking_strategy(collection_name)
        semantic_segments = sum(
            1
            for segment in prepared_segments
            if str(segment.get('chunking_strategy_effective') or '').strip().lower() == 'semantic'
        )
        fixed_segments = sum(
            1
            for segment in prepared_segments
            if str(segment.get('chunking_strategy_effective') or '').strip().lower() == 'fixed'
        )
        prepared_groups = sum(1 for segment in prepared_segments if bool(segment.get('chunking_prepared')))
        merged_source_segments = sum(
            max(int(segment.get('source_segment_count') or 1) - 1, 0)
            for segment in prepared_segments
        )
        return {
            'collection_name': collection_name,
            'requested_strategy': requested_strategy,
            'source_segments': len(raw_segments),
            'prepared_segments': len(prepared_segments),
            'semantic_segments': semantic_segments,
            'fixed_segments': fixed_segments,
            'prepared_groups': prepared_groups,
            'merged_source_segments': merged_source_segments,
        }

    def reindex_documents(self, doc_ids: list[str]) -> dict[str, Any]:
        """批量重建指定文档的索引。

        Args:
            doc_ids: 需要强制重建索引的文档 ID 列表。

        Returns:
            包含成功数、失败列表和处理状态的汇总结果。
        """
        results = [self.ingest_document(doc_id, force=True) for doc_id in doc_ids]
        indexed = sum(1 for item in results if item['status'] == 'indexed')
        failed = [item for item in results if item['status'] == 'failed']
        self.trace.record('reindex', {'doc_ids': doc_ids, 'indexed': indexed, 'failed': len(failed)})
        return {'status': 'ok', 'doc_ids': doc_ids, 'indexed': indexed, 'failed': failed}

    def delete_document_chunks(self, doc_id: str) -> None:
        """删除文档对应的所有向量分块。

        Args:
            doc_id: 需要删除索引分块的文档 ID。
        """
        record = self.state.documents.get(doc_id)
        if record is None:
            return
        self.vector_store.delete_chunks(record['collection_name'], record.get('chunk_ids', []))

    def scan_directory(self, directory: str, collection_name: str, recursive: bool, file_types: list[str]) -> dict[str, Any]:
        """扫描目录并导入支持的文件类型。

        Args:
            directory: 待扫描目录路径。
            collection_name: 导入目标知识库名称。
            recursive: 是否递归扫描子目录。
            file_types: 允许导入的文件类型列表；为空时表示接收所有支持类型。

        Returns:
            包含已导入文档、失败原因和统计信息的结果字典。
        """
        path = Path(directory)
        if not path.exists():
            return {'status': 'failed', 'reason': 'directory not found'}

        normalized_types = {item.lower().lstrip('.') for item in file_types if item.strip()}
        pattern = '**/*' if recursive else '*'
        uploaded: list[DocumentRecord] = []
        failed: list[dict[str, Any]] = []
        stats = {
            'input_files': 0,
            'imported_documents': 0,
            'failed_files': 0,
            'skipped_files': 0,
        }

        for source_path in path.glob(pattern):
            if not source_path.is_file():
                continue
            stats['input_files'] += 1

            file_type = self.detect_file_type(source_path)
            if file_type not in self.SUPPORTED_TYPES:
                stats['skipped_files'] += 1
                continue
            if normalized_types and file_type not in normalized_types:
                stats['skipped_files'] += 1
                continue
            validation_error = self.validate_import_candidate(source_path.name, file_type=file_type, file_size=source_path.stat().st_size)
            if validation_error is not None:
                failed.append(validation_error)
                stats['failed_files'] += 1
                continue

            try:
                doc_records = self.import_path(source_path, collection_name, tags=[])
                uploaded.extend(doc_records)
                stats['imported_documents'] += len(doc_records)
            except Exception as exc:
                failed.append(self.build_import_failure(source_path.name, exc, file_type=file_type))
                stats['failed_files'] += 1

        self.trace.record(
            'scan_directory',
            {'collection_name': collection_name, 'count': len(uploaded), 'failed': len(failed), 'stats': stats},
        )
        return {
            'status': 'ok',
            'collection_name': collection_name,
            'uploaded': uploaded,
            'failed': failed,
            'stats': stats,
        }

    def import_path(self, source_path: Path, collection_name: str, tags: list[str]) -> list[DocumentRecord]:
        """按文件类型导入单个文件或 ZIP 内多文件。

        Args:
            source_path: 待导入的源文件路径。
            collection_name: 导入目标知识库名称。
            tags: 需要附加到文档记录上的标签列表。

        Returns:
            导入后生成的文档记录列表。
        """
        if self.detect_file_type(source_path) == 'zip':
            return self.import_archive(source_path, collection_name, tags)
        return [self.import_file(source_path, collection_name, tags)]

    def import_archive(self, archive_path: Path, collection_name: str, tags: list[str]) -> list[DocumentRecord]:
        """解包 ZIP，并把内部可支持文件作为独立文档导入。

        Args:
            archive_path: 待导入的 ZIP 文件路径。
            collection_name: 导入目标知识库名称。
            tags: 需要附加到文档记录上的标签列表。

        Returns:
            从压缩包中导入得到的文档记录列表。

        Raises:
            ValueError: 当压缩包中没有任何可导入文件时抛出。
        """
        imported: list[DocumentRecord] = []
        archive_root = archive_path.stem or f'archive-{uuid4().hex[:6]}'
        source_archive = archive_path.name
        with tempfile.TemporaryDirectory(prefix='rag-zip-import-') as tmp_dir_name:
            extracted_root = Path(tmp_dir_name) / archive_root
            # 先安全解包到临时目录，再按成员文件逐个导入，避免污染上传目录结构。
            extracted_members = self._extract_archive_members(archive_path, extracted_root)
            for member_name, extracted_path, _ in extracted_members:
                relative_path = Path(member_name)
                normalized_relative = Path(*[part for part in relative_path.parts if part not in {'', '.'}])
                if not normalized_relative.parts:
                    continue
                doc_record = self.import_file(
                    extracted_path,
                    collection_name,
                    tags,
                    destination_subpath=Path(archive_root) / normalized_relative,
                    extra_record_fields={
                        'source_archive': source_archive,
                        'archive_member_path': normalized_relative.as_posix(),
                        'archive_member_display_path': ' > '.join(normalized_relative.parts),
                    },
                )
                imported.append(doc_record)
        if not imported:
            raise ValueError('zip contains no supported extractable files')
        return imported

    def import_file(
        self,
        source_path: Path,
        collection_name: str,
        tags: list[str],
        destination_subpath: Path | None = None,
        extra_record_fields: dict[str, Any] | None = None,
    ) -> DocumentRecord:
        """把外部文件复制到上传目录并建立索引。

        Args:
            source_path: 待导入文件路径。
            collection_name: 导入目标知识库名称。
            tags: 需要附加到文档记录上的标签列表。
            destination_subpath: 可选的目标相对路径，用于保留压缩包内层级结构。
            extra_record_fields: 需要额外写入文档记录的字段，例如压缩包来源信息。

        Returns:
            导入并完成索引后的文档记录。
        """
        self.ensure_data_dirs()
        destination_dir = self.settings.uploads_dir / collection_name
        destination_dir.mkdir(parents=True, exist_ok=True)

        content = source_path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        # 先按 checksum 去重，避免同一文件被重复复制和重复建立索引。
        existing = self._find_document_by_checksum(collection_name, checksum)
        if existing is not None:
            result = self.ingest_document(existing['doc_id'], force=False)
            return self.state.documents[result['doc_id']]

        target_path = self._resolve_import_target_path(destination_dir, source_path, destination_subpath)
        if source_path.resolve() != target_path.resolve():
            shutil.copy2(source_path, target_path)

        now = datetime.now(timezone.utc)
        doc_id = f'doc-{uuid4().hex[:8]}'
        record: DocumentRecord = {
            'doc_id': doc_id,
            'file_name': target_path.name,
            'file_path': str(target_path),
            'file_type': self.detect_file_type(target_path),
            'collection_name': collection_name,
            'tags': tags,
            'checksum': checksum,
            'status': 'uploaded',
            'chunk_ids': [],
            'indexed_chunks': 0,
            'created_at': now,
            'updated_at': now,
            'indexed_at': None,
        }
        if extra_record_fields:
            record.update(cast(Any, extra_record_fields))
        self.state.documents[doc_id] = record
        self._persist_document(record)
        self.ingest_document(doc_id, force=True)
        return self.state.documents[doc_id]

    def _extract_segments(self, file_path: Path) -> list[dict[str, Any]]:
        """按文件类型提取可供分块的文本片段。

        Args:
            file_path: 待解析文件路径。

        Returns:
            统一结构的原始文本片段列表。

        Raises:
            ValueError: 当文件类型不被当前摄取链路支持时抛出。
        """
        suffix = self.detect_file_type(file_path)
        if suffix == 'pdf':
            return self._read_pdf(file_path)
        if suffix == 'docx':
            return self._read_docx(file_path)
        if suffix == 'pptx':
            return self._read_pptx(file_path)
        if suffix == 'csv':
            return self._read_csv(file_path)
        if suffix == 'xlsx':
            return self._read_xlsx(file_path)
        if suffix == 'zip':
            return self._read_zip(file_path)
        if suffix == 'doc':
            return self._read_legacy_office(file_path, target_suffix='docx')
        if suffix == 'ppt':
            return self._read_legacy_office(file_path, target_suffix='pptx')
        if suffix in {'html', 'htm'}:
            return [{'text': self._read_html(file_path), 'page': None, 'section_title': None}]
        if suffix == 'md':
            return self._read_markdown(file_path)
        if suffix in self.TEXT_LIKE_TYPES:
            return self._read_text_file(file_path, suffix)
        if suffix in self.TEXT_LIKE_NAMES:
            return self._read_text_file(file_path, suffix)
        if suffix in self.IMAGE_TYPES:
            return self._read_image(file_path, suffix)
        if suffix in self.AUDIO_TYPES or suffix in self.VIDEO_TYPES:
            return self._read_media(file_path, suffix)
        raise ValueError(f'unsupported file type: {suffix}')

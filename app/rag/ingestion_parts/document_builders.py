"""`ingestion.py` 的索引文档构建子模块。

负责把标准化后的 segments 转换成主文档、query hint 文档和 title summary 文档，
并提供 chunk pipeline 相关的公共辅助方法。
"""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import importlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import uuid4

from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter

from app.core.config import Settings
from app.rag.ingestion_parts._typing import IngestionTypingMixin
from app.rag.llamaindex_components import build_embed_model, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import DocumentRecord

class IngestionDocumentBuilderMixin(IngestionTypingMixin):
    """封装摄取服务中的索引文档构建和 metadata 规整逻辑。"""

    def _build_documents(self, record: DocumentRecord, segments: list[dict[str, Any]]) -> list[Document]:
        """把文本片段转换为带元数据的 LlamaIndex Document。"""
        documents: list[Document] = []
        for index, segment in enumerate(segments, start=1):
            text = re.sub(r'\n{3,}', '\n\n', segment['text'].strip())
            if not text:
                continue

            parent_chunk_id = f"{record['doc_id']}-parent-{index:04d}"
            metadata = {
                'doc_id': record['doc_id'],
                'source': record['file_name'],
                'file_path': record['file_path'],
                'file_name': record['file_name'],
                'file_type': record['file_type'],
                'collection_name': record['collection_name'],
                'tags': '|'.join(record['tags']),
                'created_at': self._to_iso(record.get('created_at')),
                'updated_at': self._to_iso(record.get('updated_at')),
                'document_title': record.get('document_title'),
                'document_summary': record.get('document_summary'),
                'document_keywords': record.get('document_keywords'),
                'document_hierarchy': record.get('document_hierarchy'),
                'source_archive': record.get('source_archive'),
                'archive_member_path': record.get('archive_member_path'),
                'archive_member_display_path': record.get('archive_member_display_path'),
                'year': record.get('year'),
                'year_int': self._safe_int(record.get('year')),
                'quarter': record.get('quarter'),
                'quarter_num': self._quarter_to_int(record.get('quarter')),
                'version': record.get('version'),
                'permission': record.get('permission'),
                'page': segment.get('page'),
                'pdf_ocr_used': segment.get('pdf_ocr_used'),
                'pdf_layout_source': segment.get('pdf_layout_source'),
                'pdf_page_title': segment.get('pdf_page_title'),
                'pdf_block_role': segment.get('pdf_block_role'),
                'pdf_block_index': segment.get('pdf_block_index'),
                'pdf_block_line_count': segment.get('pdf_block_line_count'),
                'slide_number': segment.get('slide_number'),
                'ppt_title': segment.get('ppt_title'),
                'ppt_body_count': segment.get('ppt_body_count'),
                'ppt_has_notes': segment.get('ppt_has_notes'),
                'sheet_name': segment.get('sheet_name'),
                'row_count': segment.get('row_count'),
                'table_total_rows': segment.get('table_total_rows'),
                'table_columns': segment.get('table_columns'),
                'table_has_header': segment.get('table_has_header'),
                'table_row_start': segment.get('table_row_start'),
                'table_row_end': segment.get('table_row_end'),
                'media_segment_index': segment.get('media_segment_index'),
                'transcript_start_seconds': segment.get('transcript_start_seconds'),
                'transcript_end_seconds': segment.get('transcript_end_seconds'),
                'transcript_timecode': segment.get('transcript_timecode'),
                'transcript_text': segment.get('transcript_text'),
                'image_width': segment.get('image_width'),
                'image_height': segment.get('image_height'),
                'ocr_line_index': segment.get('ocr_line_index'),
                'ocr_line_count': segment.get('ocr_line_count'),
                'ocr_line_confidence': segment.get('ocr_line_confidence'),
                'ocr_line_text': segment.get('ocr_line_text'),
                'section_title': segment.get('section_title'),
                'section_level': segment.get('section_level'),
                'hierarchy_path': segment.get('hierarchy_path'),
                'code_language': segment.get('code_language'),
                'media_kind': segment.get('media_kind'),
                'archive_member_path': segment.get('archive_member_path'),
                'archive_member_type': segment.get('archive_member_type'),
                'index_kind': 'content',
                'node_level': 'child',
                'parent_chunk_id': parent_chunk_id,
                'parent_context': self._build_parent_context(record, segment, text),
                'chunking_strategy_requested': segment.get('chunking_strategy_requested'),
                'chunking_strategy_effective': segment.get('chunking_strategy_effective'),
                'chunking_prepared': segment.get('chunking_prepared'),
                'source_segment_count': segment.get('source_segment_count'),
                'segment_summary': segment.get('segment_summary'),
                'segment_keywords': segment.get('segment_keywords'),
                'chapter_tags': segment.get('chapter_tags'),
            }
            normalized_metadata = {
                key: self._normalize_document_metadata_value(value)
                for key, value in metadata.items()
                if value not in (None, '')
            }
            content_chunk_id = f"{record['doc_id']}-segment-{index:04d}"
            documents.append(
                Document(
                    text=text,
                    metadata=normalized_metadata,
                    id_=content_chunk_id,
                )
            )
            parent_text = str(metadata.get('parent_context') or '').strip()
            if parent_text:
                # parent 文档为长上下文回填服务，检索命中后仍会映射回真实内容块。
                parent_metadata = {
                    **metadata,
                    'index_kind': 'parent',
                    'node_level': 'parent',
                    'retrieval_target_chunk_id': parent_chunk_id,
                    'retrieval_target_text': parent_text,
                    'child_chunk_id': content_chunk_id,
                }
                normalized_parent_metadata = {
                    key: self._normalize_document_metadata_value(value)
                    for key, value in parent_metadata.items()
                    if value not in (None, '')
                }
                documents.append(
                    Document(
                        text=parent_text,
                        metadata=normalized_parent_metadata,
                        id_=parent_chunk_id,
                    )
                )
            documents.extend(
                self._build_query_hint_documents(
                    record=record,
                    segment=segment,
                    text=text,
                    content_chunk_id=content_chunk_id,
                    parent_chunk_id=parent_chunk_id,
                    metadata=metadata,
                    index=index,
                )
            )
            documents.extend(
                self._build_title_summary_documents(
                    record=record,
                    segment=segment,
                    text=text,
                    content_chunk_id=content_chunk_id,
                    parent_chunk_id=parent_chunk_id,
                    metadata=metadata,
                    index=index,
                )
            )

        if not documents:
            raise ValueError('document contains no indexable text')
        return documents

    def _normalize_document_metadata_value(self, value: Any) -> Any:
        """把 list 等复杂 metadata 序列化为向量库可接受的标量。"""
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return '|'.join(items)
        return value

    def _build_query_hint_documents(
        self,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
        content_chunk_id: str,
        parent_chunk_id: str,
        metadata: dict[str, Any],
        index: int,
    ) -> list[Document]:
        """为每个内容块生成问题导向的检索子索引文档。"""
        hints = self._build_query_hints(record, segment, text)
        documents: list[Document] = []
        for hint_index, hint in enumerate(hints, start=1):
            hint_metadata = {
                **metadata,
                'index_kind': 'query_hint',
                'retrieval_target_chunk_id': content_chunk_id,
                'retrieval_target_text': text,
                'query_hint_text': hint,
                'parent_chunk_id': parent_chunk_id,
            }
            normalized_hint_metadata = {
                key: self._normalize_document_metadata_value(value)
                for key, value in hint_metadata.items()
                if value not in (None, '')
            }
            documents.append(
                Document(
                    text=hint,
                    metadata=normalized_hint_metadata,
                    id_=f"{record['doc_id']}-query-{index:04d}-{hint_index:02d}",
                )
            )
        return documents

    def _build_title_summary_documents(
        self,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
        content_chunk_id: str,
        parent_chunk_id: str,
        metadata: dict[str, Any],
        index: int,
    ) -> list[Document]:
        """为每个内容块生成标题摘要向量入口，提升多向量召回。"""
        document_title = str(record.get('document_title') or '').strip()
        section_title = str(segment.get('section_title') or '').strip()
        hierarchy_path = str(segment.get('hierarchy_path') or '').strip()
        summary = str(segment.get('segment_summary') or '').strip()
        keyword_items = [str(item).strip() for item in segment.get('segment_keywords') or [] if str(item).strip()]
        chapter_tags = [str(item).strip() for item in segment.get('chapter_tags') or [] if str(item).strip()]
        title_lines = [
            f'文档标题：{document_title}' if document_title else '',
            f'章节标题：{section_title}' if section_title else '',
            f'层级路径：{hierarchy_path}' if hierarchy_path else '',
            f'摘要：{summary}' if summary else '',
            f"关键词：{'、'.join(keyword_items[:5])}" if keyword_items else '',
            f"主题标签：{'、'.join(chapter_tags[:3])}" if chapter_tags else '',
        ]
        title_text = '\n'.join(line for line in title_lines if line).strip()
        if len(title_text) < 12:
            return []
        title_metadata = {
            **metadata,
            'index_kind': 'title_summary',
            'node_level': 'child_aux',
            'retrieval_target_chunk_id': content_chunk_id,
            'retrieval_target_text': text,
            'parent_chunk_id': parent_chunk_id,
        }
        normalized_title_metadata = {
            key: self._normalize_document_metadata_value(value)
            for key, value in title_metadata.items()
            if value not in (None, '')
        }
        return [
            Document(
                text=title_text,
                metadata=normalized_title_metadata,
                id_=f"{record['doc_id']}-title-{index:04d}",
            )
        ]

    def _build_parent_context(self, record: DocumentRecord, segment: dict[str, Any], text: str) -> str:
        """构建 small-to-big 场景下可回填的父块上下文。"""
        heading = str(segment.get('hierarchy_path') or segment.get('section_title') or '').strip()
        summary = str(segment.get('segment_summary') or record.get('document_summary') or '').strip()
        parts = [
            f"文档：{record.get('document_title')}" if record.get('document_title') else '',
            f"章节：{heading}" if heading else '',
            f"摘要：{summary}" if summary else '',
            text,
        ]
        normalized = '\n'.join(part for part in parts if part).strip()
        return normalized[:2200].strip()

    def _build_query_hints(self, record: DocumentRecord, segment: dict[str, Any], text: str) -> list[str]:
        """为 FAQ 和口语化问法生成规则式查询提示。"""
        focus = str(segment.get('section_title') or segment.get('hierarchy_path') or record.get('document_title') or '').strip()
        summary = str(segment.get('segment_summary') or '').strip()
        keywords = [str(item).strip() for item in segment.get('segment_keywords') or [] if str(item).strip()]
        chapter_tags = [str(item).strip() for item in segment.get('chapter_tags') or [] if str(item).strip()]

        raw_hints = [
            f'{focus} 是什么' if focus else '',
            f'怎么查看 {focus}' if focus else '',
            f'如何使用 {focus}' if focus else '',
            f'{focus} 有什么作用' if focus else '',
            f'{focus} 相关说明' if focus else '',
            f'{focus} 常见问题' if focus else '',
            f"{focus} {' '.join(keywords[:2])} 怎么做" if focus and keywords else '',
            f"{focus} {' '.join(chapter_tags[:2])} 怎么看" if focus and chapter_tags else '',
            summary if 8 <= len(summary) <= 80 else '',
        ]

        hints: list[str] = []
        seen: set[str] = set()
        for item in raw_hints:
            normalized = re.sub(r'\s+', ' ', item).strip(' ？?。.!')
            if len(normalized) < 4:
                continue
            dedupe_key = normalized.lower()
            if dedupe_key in seen:
                continue
            hints.append(normalized)
            seen.add(dedupe_key)
            if len(hints) >= 4:
                break
        return hints

    def _safe_int(self, value: Any) -> int | None:
        """把元数据里的数字字符串安全转成整数。"""
        if value in (None, ''):
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    def _safe_float(self, value: Any) -> float | None:
        """把元数据里的数字字符串安全转成浮点数。"""
        if value in (None, ''):
            return None
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    def _quarter_to_int(self, value: Any) -> int | None:
        """把季度标准化为 1-4。"""
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

    def _build_pipeline(self, collection_name: str) -> IngestionPipeline:
        """构建文本切分和嵌入写库的摄取流水线。"""
        # 通过主模块回读这些符号，兼容现有测试和外部 patch 仍然指向 `app.rag.ingestion` 的写法。
        from app.rag import ingestion as ingestion_module

        parser = self._build_chunk_parser(collection_name)
        transformations = [
            parser,
            self.embed_model,
        ]
        return ingestion_module.IngestionPipeline(
            transformations=transformations,
            vector_store=ingestion_module.build_vector_store(self.vector_store, collection_name),
        )

    def _build_chunk_parser(self, collection_name: str) -> Any:
        """按配置创建切块器。"""
        from app.rag import ingestion as ingestion_module

        strategy = self._resolve_chunking_strategy(collection_name)
        chunk_size = self._resolve_chunk_size(collection_name)
        chunk_overlap = self._resolve_chunk_overlap(collection_name)
        if strategy == 'semantic':
            try:
                node_parser_module = ingestion_module.importlib.import_module('llama_index.core.node_parser')
                SemanticSplitterNodeParser = getattr(node_parser_module, 'SemanticSplitterNodeParser')
                return SemanticSplitterNodeParser(
                    buffer_size=max(1, self.settings.semantic_chunk_buffer_size),
                    breakpoint_percentile_threshold=max(1, self.settings.semantic_chunk_breakpoint_percentile),
                    embed_model=self.embed_model,
                )
            except (AttributeError, ModuleNotFoundError) as exc:
                self.trace.record(
                    'semantic_chunking_fallback',
                    {
                        'collection_name': collection_name,
                        'reason': str(exc),
                    },
                )
        return ingestion_module.SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def _resolve_chunk_size(self, collection_name: str) -> int:
        """读取集合级别生效的 chunk size。"""
        collection = self.state.collections.get(collection_name)
        if collection is None:
            return self.settings.default_chunk_size
        return int(collection.get('chunk_size', self.settings.default_chunk_size))

    def _resolve_chunk_overlap(self, collection_name: str) -> int:
        """读取集合级别生效的 chunk overlap。"""
        collection = self.state.collections.get(collection_name)
        if collection is None:
            return self.settings.default_chunk_overlap
        return int(collection.get('chunk_overlap', self.settings.default_chunk_overlap))

    def _resolve_chunking_strategy(self, collection_name: str) -> str:
        """读取生效的切块策略。"""
        collection = self.state.collections.get(collection_name)
        if collection is None:
            return self.settings.ingestion_chunking_strategy
        strategy = str(collection.get('chunking_strategy') or self.settings.ingestion_chunking_strategy).strip().lower()
        return 'semantic' if strategy == 'semantic' else 'fixed'

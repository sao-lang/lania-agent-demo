"""`ingestion.py` 的分段清洗与元数据处理子模块。

负责语义分组、噪音清洗、文档元数据推断和 segment 富化，
把摄取后的规范化步骤和入口编排分离开。
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
from app.rag.llamaindex_components import build_embed_model, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import DocumentRecord
from app.rag.ingestion_parts._typing import IngestionTypingMixin


class IngestionSegmentProcessingMixin(IngestionTypingMixin):
    """放片段清洗、元数据抽取和 semantic chunk 预处理逻辑。"""

    def _prepare_segments(
        self,
        record: DocumentRecord,
        segments: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """执行清洗、元数据增强和结构化元数据抽取。"""
        cleaned_segments = self._clean_segments(segments) if self.settings.enable_noise_cleanup else segments
        doc_metadata = self._extract_document_metadata(record, cleaned_segments)
        enriched_segments = (
            self._enrich_segments(cleaned_segments, doc_metadata)
            if self.settings.enable_metadata_enrichment
            else cleaned_segments
        )
        prepared_segments = self._prepare_segments_for_chunking(record, enriched_segments)
        return prepared_segments, doc_metadata

    def _prepare_segments_for_chunking(
        self,
        record: DocumentRecord,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """在入库前按切块策略归并正文块，尽量别把结构化片段打得太碎。"""
        strategy = self._resolve_chunking_strategy(record['collection_name'])
        if strategy != 'semantic':
            return [
                {
                    **segment,
                    'chunking_strategy_requested': strategy,
                    'chunking_strategy_effective': 'fixed',
                    'chunking_prepared': False,
                    'source_segment_count': 1,
                }
                for segment in segments
            ]

        prepared: list[dict[str, Any]] = []
        pending_group: list[dict[str, Any]] = []
        target_chars = max(self._resolve_chunk_size(record['collection_name']) * 3, 1200)
        max_chars = max(self._resolve_chunk_size(record['collection_name']) * 4, target_chars)

        def flush_pending() -> None:
            """把当前待归并正文组落到 `prepared`，并清空缓存。"""
            if not pending_group:
                return
            prepared.append(
                self._merge_semantic_segments(
                    pending_group,
                    target_chars=target_chars,
                    max_chars=max_chars,
                )
            )
            pending_group.clear()

        for segment in segments:
            # 表格、OCR 行、代码块这类片段更适合保留原结构，不跟正文一起做语义归并。
            if self._segment_prefers_fixed_chunking(segment):
                flush_pending()
                prepared.append(
                    {
                        **segment,
                        'chunking_strategy_requested': strategy,
                        'chunking_strategy_effective': 'fixed',
                        'chunking_prepared': False,
                        'source_segment_count': 1,
                    }
                )
                continue

            candidate = {
                **segment,
                'chunking_strategy_requested': strategy,
                'chunking_strategy_effective': 'semantic',
                'chunking_prepared': False,
                'source_segment_count': 1,
            }
            if pending_group and not self._can_merge_semantic_segment_group(pending_group[-1], candidate, max_chars=max_chars):
                flush_pending()
            pending_group.append(candidate)

        flush_pending()
        return prepared or segments

    def _segment_prefers_fixed_chunking(self, segment: dict[str, Any]) -> bool:
        """判断一个片段是否应该保留原结构，不参与语义归并。"""
        role = str(segment.get('pdf_block_role') or '').strip().lower()
        media_kind = str(segment.get('media_kind') or '').strip().lower()
        text = str(segment.get('text') or '').strip()
        if role in self.SEMANTIC_FIXED_BLOCK_ROLES:
            return True
        if media_kind in self.SEMANTIC_FIXED_MEDIA_KINDS:
            return True
        if segment.get('code_language'):
            return True
        if segment.get('table_columns') or segment.get('table_markdown'):
            return True
        if segment.get('ocr_line_index') or segment.get('transcript_timecode'):
            return True
        return False

    def _can_merge_semantic_segment_group(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
        *,
        max_chars: int,
    ) -> bool:
        """判断相邻正文片段适不适合并成一个更稳定的语义切块输入。"""
        prev_text = str(previous.get('text') or '').strip()
        curr_text = str(current.get('text') or '').strip()
        if not prev_text or not curr_text:
            return False
        if len(prev_text) + len(curr_text) > max_chars:
            return False
        if previous.get('page') not in (None, current.get('page')):
            return False
        prev_heading = str(previous.get('hierarchy_path') or previous.get('section_title') or '').strip()
        curr_heading = str(current.get('hierarchy_path') or current.get('section_title') or '').strip()
        if prev_heading and curr_heading and prev_heading != curr_heading:
            return False
        return True

    def _merge_semantic_segments(
        self,
        segments: list[dict[str, Any]],
        *,
        target_chars: int,
        max_chars: int,
    ) -> dict[str, Any]:
        """把相邻正文片段并成更适合 semantic splitter 的长上下文。"""
        if len(segments) == 1:
            single = dict(segments[0])
            single['chunking_prepared'] = False
            return single
        merged = dict(segments[0])
        merged_text_parts = [str(item.get('text') or '').strip() for item in segments if str(item.get('text') or '').strip()]
        merged['text'] = '\n\n'.join(merged_text_parts).strip()
        merged['source_segment_count'] = len(segments)
        merged['chunking_prepared'] = True
        merged['segment_summary'] = self._summarize_text(merged['text'], max_length=140)
        merged['segment_keywords'] = self._extract_keywords(merged['text'], limit=5)
        merged['chapter_tags'] = self._extract_keywords(
            ' '.join(
                str(item)
                for item in [
                    merged.get('section_title'),
                    merged.get('hierarchy_path'),
                ]
                if str(item).strip()
            )
            or merged['text'][:120],
            limit=3,
        )
        return merged

    def _clean_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """清理页眉页脚、免责声明和跨段重复噪声。"""
        repeated_lines = self._detect_repeated_noise_lines(segments)
        cleaned: list[dict[str, Any]] = []
        for segment in segments:
            text = self._clean_noise_text(str(segment.get('text') or ''), repeated_lines)
            if not text:
                continue
            payload = dict(segment)
            payload['text'] = text
            cleaned.append(payload)
        return cleaned or segments

    def _detect_repeated_noise_lines(self, segments: list[dict[str, Any]]) -> set[str]:
        """识别在多个片段里反复出现的短噪声行。"""
        counter: Counter[str] = Counter()
        for segment in segments:
            for line in str(segment.get('text') or '').splitlines():
                normalized = re.sub(r'\s+', ' ', line).strip()
                if len(normalized) < 3 or len(normalized) > 80:
                    continue
                counter[normalized.lower()] += 1
        return {
            line
            for line, count in counter.items()
            if count >= 2
            and (
                any(pattern.match(line) for pattern in self.NOISE_LINE_PATTERNS)
                or len(line.split()) <= 6
            )
        }

    def _clean_noise_text(self, text: str, repeated_lines: set[str]) -> str:
        """对单段文本做行级降噪。"""
        cleaned_lines: list[str] = []
        for line in text.splitlines():
            normalized = re.sub(r'\s+', ' ', line).strip()
            if not normalized:
                cleaned_lines.append('')
                continue
            if normalized.lower() in repeated_lines:
                continue
            if any(pattern.match(normalized) for pattern in self.NOISE_LINE_PATTERNS):
                continue
            cleaned_lines.append(line.rstrip())
        cleaned = '\n'.join(cleaned_lines)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def _extract_document_metadata(self, record: DocumentRecord, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """从文件名、路径和正文里提取结构化元数据。"""
        file_path = Path(record['file_path'])
        file_name = str(record.get('file_name') or file_path.name)
        full_text = '\n'.join(str(segment.get('text') or '') for segment in segments)
        sample_text = f'{file_name}\n{full_text[:4000]}'
        relative_parent = self._relative_parent_path(file_path, record['collection_name'])
        document_title = self._infer_document_title(file_path, segments)
        source_archive = str(record.get('source_archive') or '').strip()
        archive_member_path = str(record.get('archive_member_path') or '').strip()
        archive_member_display_path = archive_member_path.replace('/', ' > ') if archive_member_path else None
        year = self._extract_year(sample_text)
        quarter = self._extract_quarter(sample_text)
        version = self._extract_version(sample_text)
        permission = self._extract_permission(record, sample_text, file_path)
        document_hierarchy = ' / '.join(
            item
            for item in [record['collection_name'], source_archive, archive_member_path or relative_parent, document_title]
            if item
        )
        return {
            'document_title': document_title,
            'document_summary': self._summarize_text(full_text, max_length=180),
            'document_keywords': self._extract_keywords(full_text, limit=6),
            'year': year,
            'quarter': quarter,
            'version': version,
            'permission': permission,
            'document_hierarchy': document_hierarchy or record['collection_name'],
            'source_archive': source_archive or None,
            'archive_member_path': archive_member_path or None,
            'archive_member_display_path': archive_member_display_path,
        }

    def _relative_parent_path(self, file_path: Path, collection_name: str) -> str | None:
        """获取文档在集合目录下的相对层级。"""
        try:
            relative = file_path.parent.relative_to(self.settings.uploads_dir / collection_name)
        except ValueError:
            return None
        if str(relative) == '.':
            return None
        return str(relative).replace('\\', '/')

    def _infer_document_title(self, file_path: Path, segments: list[dict[str, Any]]) -> str:
        """优先使用一级标题，否则退回文件名标题。"""
        for segment in segments:
            title = str(segment.get('section_title') or '').strip()
            if title:
                return title
        normalized = re.sub(r'[_\-]+', ' ', file_path.stem)
        return re.sub(r'\s+', ' ', normalized).strip() or file_path.stem

    def _extract_year(self, text: str) -> str | None:
        """提取四位年份。"""
        match = re.search(r'\b(20\d{2})\b', text)
        return match.group(1) if match else None

    def _extract_quarter(self, text: str) -> str | None:
        """提取季度信息并标准化为 Q1-Q4。"""
        normalized = text.upper()
        match = re.search(r'\bQ([1-4])\b', normalized)
        if match:
            return f"Q{match.group(1)}"
        match = re.search(r'([1-4])\s*季度', text)
        if match:
            return f"Q{match.group(1)}"
        return None

    def _extract_version(self, text: str) -> str | None:
        """提取版本号。"""
        match = re.search(r'(?:^|[^0-9A-Za-z])(?:V|VERSION)[\s._-]*(\d+(?:\.\d+){0,2})\b', text, re.IGNORECASE)
        if not match:
            return None
        return f"v{match.group(1)}"

    def _extract_permission(self, record: DocumentRecord, text: str, file_path: Path) -> str | None:
        """从标签、路径与正文中推断文档权限级别。"""
        tag_candidates = [self._normalize_permission_tag(item) for item in record.get('tags', [])]
        normalized_tags = [item for item in tag_candidates if item]
        if normalized_tags:
            return normalized_tags[0]

        content = '\n'.join([str(file_path), str(record.get('file_name') or ''), text])
        permission_patterns = [
            ('confidential', [r'\bconfidential\b', r'\bsecret\b', r'机密', r'保密']),
            ('restricted', [r'\brestricted\b', r'\bsensitive\b', r'受限', r'敏感']),
            ('private', [r'\bprivate\b', r'私有', r'仅自己']),
            ('internal', [r'\binternal\b', r'\bintranet\b', r'内部', r'内网']),
            ('public', [r'\bpublic\b', r'\bopen\b', r'公开', r'外部可见']),
        ]
        for permission, patterns in permission_patterns:
            for pattern in patterns:
                if re.search(pattern, content, flags=re.IGNORECASE):
                    return permission
        return None

    def _normalize_permission_tag(self, value: str) -> str | None:
        """把标签中的权限值归一化为统一枚举。"""
        normalized = value.strip().lower()
        if not normalized:
            return None
        if ':' in normalized:
            prefix, candidate = normalized.split(':', 1)
            if prefix in {'permission', 'perm', 'access', 'acl'}:
                normalized = candidate.strip()
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
        return alias_map.get(normalized)

    def _enrich_segments(self, segments: list[dict[str, Any]], doc_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """为片段补充摘要、关键词和章节主题标签。"""
        enriched: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment.get('text') or '').strip()
            if not text:
                continue
            section_title = str(segment.get('section_title') or '').strip()
            hierarchy_path = str(segment.get('hierarchy_path') or '').strip()
            title_context = ' '.join(item for item in [section_title, hierarchy_path, doc_metadata.get('document_title')] if item)
            payload = dict(segment)
            payload['segment_summary'] = self._summarize_text(text, max_length=140)
            payload['segment_keywords'] = self._extract_keywords(f'{title_context}\n{text}', limit=5)
            payload['chapter_tags'] = self._extract_keywords(title_context or text[:120], limit=3)
            enriched.append(payload)
        return enriched or segments

    def _summarize_text(self, text: str, max_length: int = 160) -> str | None:
        """用规则方式生成简短摘要。"""
        normalized = re.sub(r'\s+', ' ', text).strip()
        if not normalized:
            return None
        sentences = re.split(r'(?<=[。！？.!?])\s+|\n+', normalized)
        summary_parts: list[str] = []
        total = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            projected = total + len(sentence) + (1 if summary_parts else 0)
            if projected > max_length and summary_parts:
                break
            summary_parts.append(sentence)
            total = projected
            if total >= max_length:
                break
        summary = ' '.join(summary_parts).strip() or normalized[:max_length].strip()
        return summary[:max_length].strip()

    def _extract_keywords(self, text: str, limit: int = 5) -> list[str]:
        """基于词频提取轻量关键词。"""
        tokens = [token for token in re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower()) if len(token) >= 2]
        filtered = [token for token in tokens if token not in self.KEYWORD_STOPWORDS]
        if not filtered:
            return []
        ranked = Counter(filtered).most_common(limit * 3)
        keywords: list[str] = []
        for token, _ in ranked:
            if token in keywords:
                continue
            keywords.append(token)
            if len(keywords) >= limit:
                break
        return keywords

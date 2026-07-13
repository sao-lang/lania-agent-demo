"""RAG 系统分段处理子模块。

负责噪音清洗、元数据提取、语义归并、fixed/semantic chunking 策略。
与主应用的 `app/rag/ingestion_parts/segment_processing.py` 功能一致。
"""

from __future__ import annotations

import re
from typing import Any

from app.rag_system.ingestion.parts._typing import RagIngestionTypingMixin


class RagIngestionSegmentProcessingMixin(RagIngestionTypingMixin):
    """噪音清洗、元数据提取、语义归并、chunking 策略。"""

    NOISE_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r'^\s*[\d\-]+\s*$'),
        re.compile(r'^\s*page\s+\d+\s*$', re.IGNORECASE),
        re.compile(r'^\s*第\s*[一二三四五六七八九十百千万\d]+\s*页\s*$'),
        re.compile(r'^(confidential|机密|秘密|internal|仅供内部)\s*$', re.IGNORECASE),
        re.compile(r'^\s*[-*=#]{3,}\s*$'),
    ]

    def _clean_noise(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """移除噪音片段。"""
        cleaned: list[dict[str, Any]] = []
        for seg in segments:
            text = seg.get('text', '').strip()
            if not text:
                continue
            if any(p.search(text) for p in self.NOISE_PATTERNS):
                continue
            cleaned.append(seg)
        return cleaned

    def _extract_metadata_from_segments(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """从片段中提取文档级元数据。"""
        metadata: dict[str, Any] = {'headings': []}
        for seg in segments:
            if seg.get('role') == 'heading':
                metadata['headings'].append(seg.get('text', '')[:200])
        if metadata['headings']:
            metadata['document_title'] = metadata['headings'][0]
        return metadata

    def _chunk_fixed(self, text: str, doc_id: str, chunk_size: int = 800, chunk_overlap: int = 100) -> list[dict[str, Any]]:
        """固定大小分块。"""
        segments: list[dict[str, Any]] = []
        paragraphs = re.split(r'\n\s*\n+', text)
        current = ''
        seq = 0
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) > chunk_size and current:
                seq += 1
                segments.append({'text': current.strip(), 'seq': seq, 'doc_id': doc_id})
                overlap_start = max(0, len(current) - chunk_overlap)
                current = current[overlap_start:] + '\n' + para
            else:
                current = (current + '\n' + para) if current else para
        if current.strip():
            seq += 1
            segments.append({'text': current.strip(), 'seq': seq, 'doc_id': doc_id})
        return segments

    def _chunk_semantic(self, text: str, doc_id: str) -> list[dict[str, Any]]:
        """语义分块：按段落边界和话题变化分块。"""
        paragraphs = re.split(r'\n\s*\n+', text)
        segments: list[dict[str, Any]] = []
        seq = 0
        buffer: list[str] = []
        buffer_size = 0
        max_chunk = self.settings.default_chunk_size
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            buffer.append(para)
            buffer_size += len(para)
            if buffer_size >= max_chunk:
                seq += 1
                segments.append({'text': '\n\n'.join(buffer).strip(), 'seq': seq, 'doc_id': doc_id})
                # keep overlap
                overlap_size = 0
                overlap_buffer: list[str] = []
                for bp in reversed(buffer):
                    if overlap_size + len(bp) > max_chunk * 0.2:
                        break
                    overlap_buffer.insert(0, bp)
                    overlap_size += len(bp)
                buffer = overlap_buffer
                buffer_size = overlap_size
        if buffer:
            seq += 1
            segments.append({'text': '\n\n'.join(buffer).strip(), 'seq': seq, 'doc_id': doc_id})
        return segments

    def _prepare_segments_for_index(self, segments: list[dict[str, Any]], doc_id: str) -> list[dict[str, Any]]:
        """统一准备要入索引的分段。"""
        cleaned = self._clean_noise(segments)
        metadata = self._extract_metadata_from_segments(cleaned)
        strategy = self.settings.ingestion_chunking_strategy
        all_text = '\n\n'.join(s.get('text', '') for s in cleaned)
        if strategy == 'semantic':
            return self._chunk_semantic(all_text, doc_id)
        return self._chunk_fixed(all_text, doc_id, self.settings.default_chunk_size, self.settings.default_chunk_overlap)

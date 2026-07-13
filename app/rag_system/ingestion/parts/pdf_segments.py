"""RAG 系统 PDF 分段子模块。

负责将 PDF 版面块组装为带元数据的片段，支持 table/figure/heading。
与主应用的 `app/rag/ingestion_parts/pdf_segments.py` 功能一致。
"""

from __future__ import annotations

import re
from typing import Any

from app.rag_system.ingestion.parts._typing import RagIngestionTypingMixin


class RagIngestionPdfSegmentsMixin(RagIngestionTypingMixin):
    """PDF 块渲染、OCR 回退、table/figure/heading 片段组装。"""

    TABLE_HEADING_TERMS = {'table', '表格', '表 ', '表\t', 'schedule', '统计', '数据'}
    FIGURE_TERMS = {'figure', 'fig.', '图 ', '图片', 'chart', 'graph', 'diagram'}

    def _looks_like_table(self, text: str) -> bool:
        if not text:
            return False
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if len(lines) < 2:
            return False
        pipe_count = sum(l.count('|') for l in lines)
        space_segments = [len(re.split(r'\s{2,}', l)) for l in lines]
        avg_segments = sum(space_segments) / max(len(space_segments), 1)
        return pipe_count > 4 or avg_segments > 2.5

    def _looks_like_figure(self, block: dict[str, Any]) -> bool:
        text = block.get('text', '').strip().lower()
        if not text:
            return block.get('block_type') == 'image'
        return any(term in text for term in self.FIGURE_TERMS)

    def _looks_like_table_heading(self, text: str) -> bool:
        text_lower = text.strip().lower()
        return any(term in text_lower for term in self.TABLE_HEADING_TERMS)

    def _merge_table_with_heading(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        skip_next = False
        for idx, seg in enumerate(segments):
            if skip_next:
                skip_next = False
                continue
            text = seg.get('text', '').strip()
            if self._looks_like_table_heading(text) and idx + 1 < len(segments):
                next_seg = segments[idx + 1]
                next_text = next_seg.get('text', '').strip()
                if self._looks_like_table(next_text):
                    seg['text'] = f'{text}\n{next_text}'
                    seg['block_type'] = 'table'
                    merged.append(seg)
                    skip_next = True
                    continue
            if self._looks_like_table(text):
                seg['block_type'] = 'table'
            if self._looks_like_figure(seg):
                seg['block_type'] = 'figure'
            merged.append(seg)
        return merged

    def _finalize_pdf_segments(self, segments: list[dict[str, Any]], doc_id: str) -> list[dict[str, Any]]:
        merged = self._merge_table_with_heading(segments)
        result: list[dict[str, Any]] = []
        for idx, seg in enumerate(merged):
            text = seg.get('text', '').strip()
            if not text:
                continue
            result.append({
                'text': text,
                'seq': idx + 1,
                'doc_id': doc_id,
                'page_number': seg.get('page_number', 1),
                'block_type': seg.get('block_type', 'text'),
                'role': seg.get('role', 'body'),
                'layout_source': seg.get('layout_source', 'plain_text'),
            })
        return result

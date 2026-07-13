"""RAG 系统 PDF 版面解析子模块。

负责把原始 PDF 页面拆成可理解的文本块、表格块和图片占位信息。
与主应用的 `app/rag/ingestion_parts/pdf_layout.py` 功能一致。
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any, cast

from app.rag_system.ingestion.parts._typing import RagIngestionTypingMixin


class RagIngestionPdfLayoutMixin(RagIngestionTypingMixin):
    """PDF 版面解析、阅读顺序重排和块级归并逻辑。"""

    def _read_pdf(self, file_path: Path) -> list[dict[str, Any]]:
        """读取 PDF，并给纯扫描页准备 OCR 兜底。"""
        pypdf = importlib.import_module('pypdf')
        PdfReader = getattr(pypdf, 'PdfReader')
        reader = PdfReader(str(file_path))
        layout_pages = self._extract_pdf_layout_pages(file_path)
        segments: list[dict[str, Any]] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or '').strip()
            if layout_pages and index <= len(layout_pages):
                native_segments = self._build_pdf_native_segments(
                    layout_pages[index - 1], page_number=index, fallback_text=text,
                )
                if native_segments:
                    segments.extend(native_segments)
                    continue
            if text:
                segments.extend(self._build_pdf_plain_text_segments(text, page_number=index))
                continue
            ocr_segments = self._read_pdf_page_with_ocr(file_path, page_number=index)
            if ocr_segments:
                segments.extend(ocr_segments)
        if not segments:
            raise ValueError('pdf contains no extractable text')
        return segments

    def _extract_pdf_layout_pages(self, file_path: Path) -> list[dict[str, Any]] | None:
        """尽量用 pdfplumber 先抽一份带坐标的页面布局信息。"""
        try:
            pdfplumber = importlib.import_module('pdfplumber')
        except ModuleNotFoundError:
            return None
        open_pdf = getattr(pdfplumber, 'open', None)
        if not callable(open_pdf):
            return None
        try:
            with cast(Any, open_pdf(str(file_path))) as document:
                pages: list[dict[str, Any]] = []
                for page in getattr(document, 'pages', []):
                    extract_words = getattr(page, 'extract_words', None)
                    words = extract_words() if callable(extract_words) else []
                    images = self._extract_pdf_page_images(page)
                    pages.append({
                        'words': words or [],
                        'images': images,
                        'width': self._safe_float(getattr(page, 'width', None)) or 0.0,
                        'height': self._safe_float(getattr(page, 'height', None)) or 0.0,
                    })
                return pages
        except Exception:
            return None

    def _extract_pdf_page_images(self, page: Any) -> list[dict[str, float]]:
        raw_images = getattr(page, 'images', None) or []
        images: list[dict[str, float]] = []
        for index, item in enumerate(raw_images, start=1):
            if not isinstance(item, dict):
                continue
            x0 = self._safe_float(item.get('x0'))
            x1 = self._safe_float(item.get('x1'))
            top = self._safe_float(item.get('top'))
            bottom = self._safe_float(item.get('bottom'))
            if None in {x0, x1, top, bottom} or x1 <= x0 or bottom <= top:
                continue
            images.append({
                'image_index': float(index), 'x0': x0, 'x1': x1,
                'top': top, 'bottom': bottom, 'width': x1 - x0, 'height': bottom - top,
            })
        return images

    def _build_pdf_native_segments(self, page_layout: dict[str, Any], *, page_number: int, fallback_text: str) -> list[dict[str, Any]]:
        words = [item for item in page_layout.get('words') or [] if isinstance(item, dict)]
        if not words:
            return self._build_pdf_plain_text_segments(fallback_text, page_number=page_number) if fallback_text else []
        lines = self._build_pdf_lines_from_words(words)
        if not lines:
            return self._build_pdf_plain_text_segments(fallback_text, page_number=page_number) if fallback_text else []
        page_width = self._safe_float(page_layout.get('width')) or 0.0
        ordered_lines = self._order_pdf_lines_for_reading(lines, page_width=page_width)
        blocks = self._group_pdf_lines_into_blocks(ordered_lines, page_width=page_width)
        blocks = self._enrich_pdf_blocks(blocks, page_layout=page_layout, page_number=page_number)
        return self._build_pdf_segments_from_blocks(blocks, page_number=page_number, pdf_ocr_used=False, layout_source='pdfplumber')

    def _build_pdf_plain_text_segments(self, text: str, *, page_number: int) -> list[dict[str, Any]]:
        normalized = text.strip()
        if not normalized:
            return []
        blocks: list[dict[str, Any]] = []
        for block_index, block_text in enumerate(re.split(r'\n\s*\n+', normalized), start=1):
            cleaned = re.sub(r'\s+', ' ', block_text).strip()
            if not cleaned:
                continue
            blocks.append({
                'text': cleaned,
                'role': 'heading' if block_index == 1 and self._looks_like_pdf_heading(cleaned) else 'body',
                'block_index': block_index,
                'line_count': max(1, len([l for l in block_text.splitlines() if l.strip()])),
            })
        return self._build_pdf_segments_from_blocks(blocks, page_number=page_number, pdf_ocr_used=False, layout_source='plain_text')

    def _build_pdf_lines_from_words(self, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for word in words:
            text = str(word.get('text') or '').strip()
            if not text:
                continue
            top = round(self._safe_float(word.get('top')) or 0.0, 1)
            bottom = round(self._safe_float(word.get('bottom')) or top, 1)
            key = (int(round(top * 2)), int(round(bottom * 2)))
            grouped.setdefault(key, []).append(word)
        lines: list[dict[str, Any]] = []
        for payload in grouped.values():
            ordered = sorted(payload, key=lambda w: (self._safe_float(w.get('x0')) or 0.0, self._safe_float(w.get('x1')) or 0.0))
            text = ' '.join(str(w.get('text') or '').strip() for w in ordered if str(w.get('text') or '').strip()).strip()
            if not text:
                continue
            x0 = min(self._safe_float(w.get('x0')) or 0.0 for w in ordered)
            x1 = max(self._safe_float(w.get('x1')) or x0 for w in ordered)
            top = min(self._safe_float(w.get('top')) or 0.0 for w in ordered)
            bottom = max(self._safe_float(w.get('bottom')) or top for w in ordered)
            lines.append({'text': text, 'x0': x0, 'x1': x1, 'top': top, 'bottom': bottom, 'words': ordered})
        return sorted(lines, key=lambda l: (l['top'], l['x0']))

    def _order_pdf_lines_for_reading(self, lines: list[dict[str, Any]], *, page_width: float) -> list[dict[str, Any]]:
        if not lines or page_width <= 0:
            return sorted(lines, key=lambda l: (l['top'], l['x0']))
        threshold = page_width * 0.72
        full_width = [l for l in lines if (l['x1'] - l['x0']) >= threshold]
        narrow = [l for l in lines if l not in full_width]
        columns = self._cluster_pdf_columns(narrow, page_width=page_width)
        if len(columns) < 2:
            return sorted(lines, key=lambda l: (l['top'], l['x0']))
        ordered: list[dict[str, Any]] = []
        top_anchor = min((l['top'] for l in narrow), default=0.0)
        ordered.extend(sorted([l for l in full_width if l['top'] < top_anchor], key=lambda l: (l['top'], l['x0'])))
        for col in columns:
            ordered.extend(sorted(col, key=lambda l: (l['top'], l['x0'])))
        ordered.extend(sorted([l for l in full_width if l['top'] >= top_anchor], key=lambda l: (l['top'], l['x0'])))
        return ordered

    def _cluster_pdf_columns(self, lines: list[dict[str, Any]], *, page_width: float) -> list[list[dict[str, Any]]]:
        if len(lines) < 4 or page_width <= 0:
            return []
        threshold = max(32.0, page_width * 0.08)
        clusters: list[dict[str, Any]] = []
        for line in sorted(lines, key=lambda l: (l['x0'], l['top'])):
            anchor = float(line.get('x0') or 0.0)
            matched = next((c for c in clusters if abs(anchor - float(c['anchor'])) <= threshold), None)
            if matched is None:
                clusters.append({'anchor': anchor, 'lines': [line]})
            else:
                matched['lines'].append(line)
        return [c['lines'] for c in sorted(clusters, key=lambda c: float(c['anchor']))]

    def _group_pdf_lines_into_blocks(self, lines: list[dict[str, Any]], *, page_width: float) -> list[dict[str, Any]]:
        if not lines:
            return []
        blocks: list[dict[str, Any]] = []
        current_block: list[dict[str, Any]] = [lines[0]]
        for line in lines[1:]:
            prev = current_block[-1]
            gap = line['top'] - prev['bottom']
            x_overlap = max(0, min(prev['x1'], line['x1']) - max(prev['x0'], line['x0']))
            same_column = x_overlap > 0 or abs(prev['x0'] - line['x0']) < page_width * 0.05
            if gap < max(4.0, page_width * 0.01) and same_column:
                current_block.append(line)
            else:
                blocks.append(self._finalize_pdf_block(current_block))
                current_block = [line]
        if current_block:
            blocks.append(self._finalize_pdf_block(current_block))
        return blocks

    def _finalize_pdf_block(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        text = ' '.join(l['text'] for l in lines).strip()
        return {
            'text': text, 'role': 'heading' if self._looks_like_pdf_heading(text) else 'body',
            'x0': min(l['x0'] for l in lines), 'x1': max(l['x1'] for l in lines),
            'top': min(l['top'] for l in lines), 'bottom': max(l['bottom'] for l in lines),
            'line_count': len(lines), 'block_type': 'text',
        }

    def _enrich_pdf_blocks(self, blocks: list[dict[str, Any]], *, page_layout: dict[str, Any], page_number: int) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for block in blocks:
            enriched.append({**block, 'page_number': page_number})
        return enriched

    def _build_pdf_segments_from_blocks(self, blocks: list[dict[str, Any]], *, page_number: int, pdf_ocr_used: bool, layout_source: str) -> list[dict[str, Any]]:
        return [
            {
                'text': b['text'], 'page_number': page_number,
                'role': b.get('role', 'body'), 'block_index': idx + 1,
                'block_type': b.get('block_type', 'text'),
                'layout_source': layout_source, 'pdf_ocr_used': pdf_ocr_used,
            }
            for idx, b in enumerate(blocks) if b.get('text', '').strip()
        ]

    def _read_pdf_page_with_ocr(self, file_path: Path, *, page_number: int) -> list[dict[str, Any]]:
        try:
            import pytesseract
            from pdf2image import convert_from_path
            images = convert_from_path(str(file_path), first_page=page_number, last_page=page_number)
            if not images:
                return []
            text = pytesseract.image_to_string(images[0], lang='eng+chi_sim')
            return self._build_pdf_plain_text_segments(text.strip(), page_number=page_number) if text.strip() else []
        except Exception:
            return []

    def _looks_like_pdf_heading(self, text: str) -> bool:
        if not text:
            return False
        upper_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
        return upper_ratio > 0.5 or len(text) < 60

    def _safe_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

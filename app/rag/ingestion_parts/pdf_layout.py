"""`ingestion.py` 的 PDF 版面解析子模块。

负责把原始 PDF 页面拆成可理解的文本块、表格块和图片占位信息，
让主摄取服务只关注编排而不必承担所有版面细节。
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
from typing import Any, cast
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


class IngestionPdfLayoutMixin(IngestionTypingMixin):
    """放 PDF 版面解析、阅读顺序重排和块级归并逻辑。"""

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
                    layout_pages[index - 1],
                    page_number=index,
                    fallback_text=text,
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
        """尽量用 `pdfplumber` 先抽一份带坐标的页面布局信息。"""
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
                    pages.append(
                        {
                            'words': words or [],
                            'images': images,
                            'width': self._safe_float(getattr(page, 'width', None)) or 0.0,
                            'height': self._safe_float(getattr(page, 'height', None)) or 0.0,
                        }
                    )
                return pages
        except Exception:
            return None

    def _extract_pdf_page_images(self, page: Any) -> list[dict[str, float]]:
        """从 `pdfplumber` 页面对象里提取可排序的图片区块。"""
        raw_images = getattr(page, 'images', None) or []
        images: list[dict[str, float]] = []
        for index, item in enumerate(raw_images, start=1):
            if not isinstance(item, dict):
                continue
            x0 = self._safe_float(item.get('x0'))
            x1 = self._safe_float(item.get('x1'))
            top = self._safe_float(item.get('top'))
            bottom = self._safe_float(item.get('bottom'))
            if None in {x0, x1, top, bottom}:
                continue
            if x1 <= x0 or bottom <= top:
                continue
            images.append(
                {
                    'image_index': float(index),
                    'x0': x0,
                    'x1': x1,
                    'top': top,
                    'bottom': bottom,
                    'width': x1 - x0,
                    'height': bottom - top,
                }
            )
        return images

    def _build_pdf_native_segments(
        self,
        page_layout: dict[str, Any],
        *,
        page_number: int,
        fallback_text: str,
    ) -> list[dict[str, Any]]:
        """把带坐标的 PDF 页面整理成按阅读顺序排列的块。"""
        words = [item for item in page_layout.get('words') or [] if isinstance(item, dict)]
        if not words:
            if not fallback_text:
                return []
            return self._build_pdf_plain_text_segments(fallback_text, page_number=page_number)
        lines = self._build_pdf_lines_from_words(words)
        if not lines:
            if not fallback_text:
                return []
            return self._build_pdf_plain_text_segments(fallback_text, page_number=page_number)
        page_width = self._safe_float(page_layout.get('width')) or 0.0
        ordered_lines = self._order_pdf_lines_for_reading(lines, page_width=page_width)
        blocks = self._group_pdf_lines_into_blocks(ordered_lines, page_width=page_width)
        blocks = self._enrich_pdf_blocks(
            blocks,
            page_layout=page_layout,
            page_number=page_number,
        )
        return self._build_pdf_segments_from_blocks(
            blocks,
            page_number=page_number,
            pdf_ocr_used=False,
            layout_source='pdfplumber',
        )

    def _build_pdf_plain_text_segments(self, text: str, *, page_number: int) -> list[dict[str, Any]]:
        """给拿不到版面坐标的 PDF 文本生成基础段落块。"""
        normalized = text.strip()
        if not normalized:
            return []
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return []
        blocks: list[dict[str, Any]] = []
        for block_index, block_text in enumerate(re.split(r'\n\s*\n+', normalized), start=1):
            cleaned = re.sub(r'\s+', ' ', block_text).strip()
            if not cleaned:
                continue
            blocks.append(
                {
                    'text': cleaned,
                    'role': 'heading' if block_index == 1 and self._looks_like_pdf_heading(cleaned) else 'body',
                    'block_index': block_index,
                    'line_count': max(1, len([line for line in block_text.splitlines() if line.strip()])),
                }
            )
        return self._build_pdf_segments_from_blocks(
            blocks,
            page_number=page_number,
            pdf_ocr_used=False,
            layout_source='plain_text',
        )

    def _build_pdf_lines_from_words(self, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按坐标把 PDF 单词聚成文本行。"""
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
            ordered_words = sorted(
                payload,
                key=lambda item: (
                    self._safe_float(item.get('x0')) or 0.0,
                    self._safe_float(item.get('x1')) or 0.0,
                ),
            )
            text = ' '.join(str(item.get('text') or '').strip() for item in ordered_words if str(item.get('text') or '').strip()).strip()
            if not text:
                continue
            x0 = min(self._safe_float(item.get('x0')) or 0.0 for item in ordered_words)
            x1 = max(self._safe_float(item.get('x1')) or x0 for item in ordered_words)
            top = min(self._safe_float(item.get('top')) or 0.0 for item in ordered_words)
            bottom = max(self._safe_float(item.get('bottom')) or top for item in ordered_words)
            lines.append(
                {
                    'text': text,
                    'x0': x0,
                    'x1': x1,
                    'top': top,
                    'bottom': bottom,
                    'words': [
                        {
                            'text': str(item.get('text') or '').strip(),
                            'x0': self._safe_float(item.get('x0')) or 0.0,
                            'x1': self._safe_float(item.get('x1')) or 0.0,
                            'top': self._safe_float(item.get('top')) or 0.0,
                            'bottom': self._safe_float(item.get('bottom')) or 0.0,
                        }
                        for item in ordered_words
                        if str(item.get('text') or '').strip()
                    ],
                }
            )
        return sorted(lines, key=lambda item: (item['top'], item['x0']))

    def _order_pdf_lines_for_reading(self, lines: list[dict[str, Any]], *, page_width: float) -> list[dict[str, Any]]:
        """按页内阅读顺序重排文本行，优先照顾双栏页。"""
        if not lines:
            return []
        if page_width <= 0:
            return sorted(lines, key=lambda item: (item['top'], item['x0']))
        full_width_threshold = page_width * 0.72
        full_width = [item for item in lines if (item['x1'] - item['x0']) >= full_width_threshold]
        narrow_lines = [item for item in lines if item not in full_width]
        columns = self._cluster_pdf_columns(narrow_lines, page_width=page_width)
        if len(columns) < 2:
            return sorted(lines, key=lambda item: (item['top'], item['x0']))

        ordered: list[dict[str, Any]] = []
        top_anchor = min((item['top'] for item in narrow_lines), default=0.0)
        ordered.extend(sorted([item for item in full_width if item['top'] < top_anchor], key=lambda item: (item['top'], item['x0'])))
        for column in columns:
            ordered.extend(sorted(column, key=lambda item: (item['top'], item['x0'])))
        ordered.extend(sorted([item for item in full_width if item['top'] >= top_anchor], key=lambda item: (item['top'], item['x0'])))
        return ordered

    def _cluster_pdf_columns(self, lines: list[dict[str, Any]], *, page_width: float) -> list[list[dict[str, Any]]]:
        """按横向起点聚类列，尽量覆盖双栏和三栏页。"""
        if len(lines) < 4 or page_width <= 0:
            return []
        threshold = max(32.0, page_width * 0.08)
        clusters: list[dict[str, Any]] = []
        for line in sorted(lines, key=lambda item: (item['x0'], item['top'])):
            anchor = float(line.get('x0') or 0.0)
            matched: dict[str, Any] | None = None
            for cluster in clusters:
                if abs(anchor - float(cluster['anchor'])) <= threshold:
                    matched = cluster
                    break
            if matched is None:
                clusters.append({'anchor': anchor, 'lines': [line]})
                continue
            matched['lines'].append(line)
            line_count = len(matched['lines'])
            matched['anchor'] = ((float(matched['anchor']) * (line_count - 1)) + anchor) / line_count
        normalized = [
            sorted(cluster['lines'], key=lambda item: (item['top'], item['x0']))
            for cluster in sorted(clusters, key=lambda item: float(item['anchor']))
            if len(cluster['lines']) >= 2
        ]
        if len(normalized) < 2:
            return []
        anchors = [min(item['x0'] for item in cluster) for cluster in normalized]
        if max(anchors) - min(anchors) < page_width * 0.18:
            return []
        return normalized[:3]

    def _group_pdf_lines_into_blocks(self, lines: list[dict[str, Any]], *, page_width: float) -> list[dict[str, Any]]:
        """把重排后的文本行继续聚成段落、标题、表格等块。"""
        if not lines:
            return []
        blocks: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in lines:
            text = str(line.get('text') or '').strip()
            if not text:
                continue
            role = self._classify_pdf_line_role(text)
            if role == 'heading':
                if current:
                    blocks.append(current)
                current = self._start_pdf_block(line, role='heading')
                continue
            if current is None:
                current = self._start_pdf_block(line, role=role)
                continue

            gap = (line['top'] - current['bottom']) if current.get('bottom') is not None else 0.0
            indent_delta = abs((line['x0'] or 0.0) - (current.get('x0') or 0.0))
            current_role = str(current.get('role') or 'body')
            gap_threshold = 30.0 if role == current_role == 'table_like' else 18.0
            should_split = (
                role != current_role
                or gap > gap_threshold
                or indent_delta > max(28.0, page_width * 0.08 if page_width else 28.0)
            )
            if should_split:
                blocks.append(current)
                current = self._start_pdf_block(line, role=role)
                continue
            current['lines'].append(text)
            current['items'].append(line)
            current['bottom'] = max(current.get('bottom') or line['bottom'], line['bottom'])
            current['x0'] = min(current.get('x0') or line['x0'], line['x0'])
            current['x1'] = max(current.get('x1') or line['x1'], line['x1'])
        if current:
            blocks.append(current)
        for index, block in enumerate(blocks, start=1):
            block['block_index'] = index
            block['text'] = '\n'.join(block.get('lines') or []).strip()
            block['line_count'] = len(block.get('lines') or [])
        return [block for block in blocks if str(block.get('text') or '').strip()]

    def _start_pdf_block(self, line: dict[str, Any], *, role: str) -> dict[str, Any]:
        """初始化一个 PDF 文本块。"""
        return {
            'role': role,
            'lines': [str(line.get('text') or '').strip()],
            'items': [line],
            'top': line.get('top'),
            'bottom': line.get('bottom'),
            'x0': line.get('x0'),
            'x1': line.get('x1'),
        }

    def _enrich_pdf_blocks(
        self,
        blocks: list[dict[str, Any]],
        *,
        page_layout: dict[str, Any],
        page_number: int,
    ) -> list[dict[str, Any]]:
        """补充 PDF 表格结构和图片区域信息。"""
        merged_blocks = self._merge_pdf_table_header_blocks(blocks)
        enriched = [self._enrich_pdf_table_block(block, page_number=page_number) for block in merged_blocks]
        enriched = self._inject_pdf_figure_blocks(
            enriched,
            images=[item for item in page_layout.get('images') or [] if isinstance(item, dict)],
        )
        for index, block in enumerate(enriched, start=1):
            block['block_index'] = index
        return enriched

    def _merge_pdf_table_header_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把紧邻表格的列头块并入 table_like，减少误拆分。"""
        if not blocks:
            return []
        merged: list[dict[str, Any]] = []
        index = 0
        while index < len(blocks):
            current = dict(blocks[index])
            next_block = blocks[index + 1] if index + 1 < len(blocks) else None
            if next_block is not None and self._should_merge_pdf_table_header(current, next_block):
                merged_table = dict(next_block)
                merged_table['lines'] = [*(current.get('lines') or []), *(next_block.get('lines') or [])]
                merged_table['items'] = [*(current.get('items') or []), *(next_block.get('items') or [])]
                merged_table['top'] = min(
                    self._safe_float(current.get('top')) or 0.0,
                    self._safe_float(next_block.get('top')) or 0.0,
                )
                merged_table['x0'] = min(
                    self._safe_float(current.get('x0')) or 0.0,
                    self._safe_float(next_block.get('x0')) or 0.0,
                )
                merged_table['x1'] = max(
                    self._safe_float(current.get('x1')) or 0.0,
                    self._safe_float(next_block.get('x1')) or 0.0,
                )
                merged_table['line_count'] = len(merged_table['lines'])
                merged.append(merged_table)
                index += 2
                continue
            merged.append(current)
            index += 1
        return merged

    def _should_merge_pdf_table_header(self, candidate: dict[str, Any], table_block: dict[str, Any]) -> bool:
        """判断一个前置块是否更像 table_like 的表头。"""
        if table_block.get('role') != 'table_like':
            return False
        if candidate.get('role') not in {'body', 'heading'}:
            return False
        if int(candidate.get('line_count') or 0) != 1:
            return False
        candidate_top = self._safe_float(candidate.get('top'))
        candidate_bottom = self._safe_float(candidate.get('bottom'))
        table_top = self._safe_float(table_block.get('top'))
        if candidate_top is None or candidate_bottom is None or table_top is None:
            return False
        if candidate_top >= table_top or (table_top - candidate_bottom) > 28:
            return False
        candidate_items = [item for item in candidate.get('items') or [] if isinstance(item, dict)]
        table_items = [item for item in table_block.get('items') or [] if isinstance(item, dict)]
        if len(candidate_items) != 1 or len(table_items) < 2:
            return False
        header_cells = self._extract_pdf_table_rows(candidate_items)
        table_rows = self._extract_pdf_table_rows(table_items)
        if len(header_cells) != 1 or len(table_rows) < 2:
            return False
        header_row = header_cells[0]
        if len([cell for cell in header_row if cell]) < 2:
            return False
        if any(re.search(r'\d', cell) for cell in header_row if cell):
            return False
        data_row_width = max((len([cell for cell in row if cell]) for row in table_rows), default=0)
        header_width = len([cell for cell in header_row if cell])
        if data_row_width == 0 or abs(header_width - data_row_width) > 1:
            return False
        return True

    def _enrich_pdf_table_block(self, block: dict[str, Any], *, page_number: int) -> dict[str, Any]:
        """将 table_like 块提升为更结构化的表格输出。"""
        if block.get('role') != 'table_like':
            return block
        items = [item for item in block.get('items') or [] if isinstance(item, dict)]
        rows = self._extract_pdf_table_rows(items)
        if not rows:
            return block
        header, data_rows, has_header = self._detect_table_header(rows)
        if not header:
            column_count = max((len(row) for row in rows), default=0)
            header = [f'column_{index}' for index in range(1, max(1, column_count) + 1)]
        header = [self._normalize_table_header_name(value, index) for index, value in enumerate(header, start=1)]
        table_rows = data_rows if has_header else rows
        block_copy = dict(block)
        block_copy['table_columns'] = header
        block_copy['table_has_header'] = has_header
        block_copy['table_total_rows'] = len(table_rows)
        block_copy['table_row_start'] = 1
        block_copy['table_row_end'] = len(table_rows)
        block_copy['table_markdown'] = self._render_pdf_table_markdown(header, table_rows)
        block_copy['table_cells_json'] = json.dumps(rows, ensure_ascii=True)
        block_copy['text'] = self._render_pdf_table_block_text(
            page_number=page_number,
            header=header,
            rows=table_rows,
            has_header=has_header,
        )
        block_copy['line_count'] = len(items)
        return block_copy

    def _extract_pdf_table_rows(self, line_items: list[dict[str, Any]]) -> list[list[str]]:
        """根据 PDF 行中的单词坐标近似恢复表格单元格。"""
        if not line_items:
            return []
        word_centers: list[float] = []
        for line in line_items:
            for word in line.get('words') or []:
                center = ((self._safe_float(word.get('x0')) or 0.0) + (self._safe_float(word.get('x1')) or 0.0)) / 2
                word_centers.append(center)
        if not word_centers:
            return []
        word_centers.sort()
        threshold = max(24.0, (max(word_centers) - min(word_centers)) * 0.08 if len(word_centers) > 1 else 24.0)
        anchors: list[float] = []
        for center in word_centers:
            if not anchors or abs(center - anchors[-1]) > threshold:
                anchors.append(center)
            else:
                anchors[-1] = (anchors[-1] + center) / 2
        rows: list[list[str]] = []
        for line in line_items:
            words = [word for word in line.get('words') or [] if str(word.get('text') or '').strip()]
            if not words:
                continue
            cells = [''] * len(anchors)
            for word in words:
                center = ((self._safe_float(word.get('x0')) or 0.0) + (self._safe_float(word.get('x1')) or 0.0)) / 2
                column_index = min(range(len(anchors)), key=lambda index: abs(anchors[index] - center))
                text = str(word.get('text') or '').strip()
                if not text:
                    continue
                cells[column_index] = f"{cells[column_index]} {text}".strip() if cells[column_index] else text
            normalized_cells = [cell.strip() for cell in cells]
            while normalized_cells and not normalized_cells[-1]:
                normalized_cells.pop()
            if any(normalized_cells):
                rows.append(normalized_cells)
        return rows

    def _render_pdf_table_markdown(self, header: list[str], rows: list[list[str]]) -> str:
        """把 PDF 表格重建结果转成 Markdown，便于检索和人工核对。"""
        if not header:
            return ''
        lines = [
            f"| {' | '.join(header)} |",
            f"| {' | '.join(['---'] * len(header))} |",
        ]
        for row in rows:
            values = list(row) + [''] * max(0, len(header) - len(row))
            lines.append(f"| {' | '.join(values[: len(header)])} |")
        return '\n'.join(lines).strip()

    def _render_pdf_table_block_text(
        self,
        *,
        page_number: int,
        header: list[str],
        rows: list[list[str]],
        has_header: bool,
    ) -> str:
        """渲染 PDF 表格块，保留列头、行值和 Markdown 视图。"""
        lines = [
            f'PDF 页面：{page_number}',
            '表格块：',
            f"列头：{' | '.join(header)}",
            f"表头识别：{'yes' if has_header else 'no'}",
            f'数据行数：{len(rows)}',
        ]
        for row_index, row in enumerate(rows, start=1):
            values = list(row) + [''] * max(0, len(header) - len(row))
            plain_row = ' '.join(value for value in values[: len(header)] if value).strip()
            if plain_row:
                lines.append(f'原始行：{plain_row}')
            pairs = [f'{column}={value}' for column, value in zip(header, values, strict=False) if value]
            if pairs:
                lines.append(f"第{row_index}行：{'; '.join(pairs)}")
        markdown = self._render_pdf_table_markdown(header, rows)
        if markdown:
            lines.append('Markdown：')
            lines.append(markdown)
        return '\n'.join(lines).strip()

    def _inject_pdf_figure_blocks(self, blocks: list[dict[str, Any]], images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """根据图片区块和图注生成可检索的图片区域块。"""
        if not images:
            return blocks
        enriched = list(blocks)
        for image in images:
            caption_block = self._match_pdf_figure_caption(blocks, image)
            nearby_body = self._find_pdf_nearby_body_block(blocks, image)
            caption_text = str(caption_block.get('text') or '').strip() if caption_block else None
            nearby_text = str(nearby_body.get('text') or '').strip() if nearby_body else None
            enriched.append(
                {
                    'role': 'figure',
                    'text': self._render_pdf_figure_block_text(image, caption_text=caption_text, nearby_text=nearby_text),
                    'top': image.get('top'),
                    'bottom': image.get('bottom'),
                    'x0': image.get('x0'),
                    'x1': image.get('x1'),
                    'line_count': 1,
                    'figure_caption': caption_text,
                    'figure_related_text': nearby_text,
                    'figure_bbox': self._serialize_pdf_bbox(image),
                    'figure_region_index': self._safe_int(image.get('image_index')) or 0,
                }
            )
        return enriched

    def _match_pdf_figure_caption(self, blocks: list[dict[str, Any]], image: dict[str, Any]) -> dict[str, Any] | None:
        """找到最接近图片区块的图注。"""
        candidates = [block for block in blocks if block.get('role') == 'figure_caption']
        if not candidates:
            return None
        best_block: dict[str, Any] | None = None
        best_distance: float | None = None
        for block in candidates:
            overlap = self._pdf_horizontal_overlap_ratio(block, image)
            if overlap < 0.2:
                continue
            distance = min(
                abs((self._safe_float(block.get('top')) or 0.0) - (self._safe_float(image.get('bottom')) or 0.0)),
                abs((self._safe_float(block.get('bottom')) or 0.0) - (self._safe_float(image.get('top')) or 0.0)),
            )
            if distance > 140:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_block = block
        return best_block

    def _find_pdf_nearby_body_block(self, blocks: list[dict[str, Any]], image: dict[str, Any]) -> dict[str, Any] | None:
        """找到与图片区块关联度最高的正文块。"""
        candidates = [block for block in blocks if block.get('role') == 'body']
        best_block: dict[str, Any] | None = None
        best_score: float | None = None
        for block in candidates:
            overlap = self._pdf_horizontal_overlap_ratio(block, image)
            vertical_gap = min(
                abs((self._safe_float(block.get('top')) or 0.0) - (self._safe_float(image.get('bottom')) or 0.0)),
                abs((self._safe_float(block.get('bottom')) or 0.0) - (self._safe_float(image.get('top')) or 0.0)),
            )
            if overlap < 0.2 and vertical_gap > 90:
                continue
            score = (overlap * 1000) - vertical_gap
            if best_score is None or score > best_score:
                best_score = score
                best_block = block
        return best_block

    def _pdf_horizontal_overlap_ratio(self, block: dict[str, Any], image: dict[str, Any]) -> float:
        """计算块与图片在横向上的重叠比例。"""
        block_x0 = self._safe_float(block.get('x0')) or 0.0
        block_x1 = self._safe_float(block.get('x1')) or block_x0
        image_x0 = self._safe_float(image.get('x0')) or 0.0
        image_x1 = self._safe_float(image.get('x1')) or image_x0
        overlap = max(0.0, min(block_x1, image_x1) - max(block_x0, image_x0))
        width = max(1.0, min(block_x1 - block_x0, image_x1 - image_x0))
        return overlap / width

    def _serialize_pdf_bbox(self, payload: dict[str, Any]) -> str:
        """把 PDF bbox 序列化为稳定字符串。"""
        x0 = round(self._safe_float(payload.get('x0')) or 0.0, 1)
        top = round(self._safe_float(payload.get('top')) or 0.0, 1)
        x1 = round(self._safe_float(payload.get('x1')) or 0.0, 1)
        bottom = round(self._safe_float(payload.get('bottom')) or 0.0, 1)
        return f'{x0},{top},{x1},{bottom}'

    def _classify_pdf_line_role(self, text: str) -> str:
        """识别 PDF 行更像标题、表格、图片说明还是正文。"""
        normalized = re.sub(r'\s+', ' ', text).strip()
        lowered = normalized.lower()
        if re.match(r'^(figure|fig\.?|图|图片|表|table)\s*[\d一二三四五六七八九十:：.-]', lowered, flags=re.IGNORECASE):
            return 'figure_caption'
        token_count = len(re.findall(r'[0-9A-Za-z_一-鿿.%/-]+', normalized))
        digit_count = len(re.findall(r'\d', normalized))
        if token_count >= 3 and digit_count >= 2:
            return 'table_like'
        if self._looks_like_pdf_heading(normalized):
            return 'heading'
        return 'body'

    def _looks_like_pdf_heading(self, text: str) -> bool:
        """用轻量规则判断一段文本是否更像标题。"""
        normalized = re.sub(r'\s+', ' ', text).strip()
        if not normalized or len(normalized) > 80:
            return False
        if '\n' in text:
            return False
        if re.match(r'^(chapter|section|part|appendix|第[一二三四五六七八九十0-9]+[章节篇部分])', normalized, flags=re.IGNORECASE):
            return True
        words = re.findall(r'[A-Za-z一-鿿0-9]+', normalized)
        if len(words) <= 12 and normalized == normalized.upper() and any(char.isalpha() for char in normalized):
            return True
        return len(normalized) <= 32 and not normalized.endswith(('。', '.', ';', '；', ':', '：'))

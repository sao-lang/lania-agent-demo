"""`ingestion.py` 的 PDF 分段与 OCR 子模块。

这里承接 PDF 版面块的二次渲染、OCR 回退和 segment 组装逻辑，
避免主文件同时堆积版面解析和摄取主流程。
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


class IngestionPdfSegmentsMixin(IngestionTypingMixin):
    """放 PDF 块渲染、OCR 回退和最终 segment 组装逻辑。"""

    def _build_pdf_segments_from_blocks(
        self,
        blocks: list[dict[str, Any]],
        *,
        page_number: int,
        pdf_ocr_used: bool,
        layout_source: str,
        media_kind: str = 'pdf',
        image_width: int | None = None,
        image_height: int | None = None,
    ) -> list[dict[str, Any]]:
        """把页内块转成更适合检索的 PDF 片段。"""
        if not blocks:
            return []
        page_title = ''
        for block in blocks:
            if block.get('role') == 'heading':
                page_title = str(block.get('text') or '').strip()
                break
        segments: list[dict[str, Any]] = []
        for index, block in enumerate(blocks, start=1):
            block_text = str(block.get('text') or '').strip()
            if not block_text:
                continue
            role = str(block.get('role') or 'body')
            section_title = block_text[:80] if role == 'heading' else (page_title[:80] if page_title else f'page {page_number}')
            hierarchy_path = page_title if role != 'heading' and page_title else None
            if role == 'table_like' and block.get('table_columns'):
                rendered = block_text
            else:
                rendered = self._render_pdf_block_text(
                    block_text,
                    role=role,
                    page_number=page_number,
                    page_title=page_title or None,
                )
            segments.append(
                {
                    'text': rendered,
                    'page': page_number,
                    'section_title': section_title,
                    'hierarchy_path': hierarchy_path,
                    'pdf_ocr_used': pdf_ocr_used,
                    'pdf_layout_source': layout_source,
                    'pdf_page_title': page_title or None,
                    'pdf_block_role': role,
                    'pdf_block_index': int(block.get('block_index') or index),
                    'pdf_block_line_count': int(block.get('line_count') or 1),
                    'figure_caption': block.get('figure_caption'),
                    'figure_related_text': block.get('figure_related_text'),
                    'figure_bbox': block.get('figure_bbox'),
                    'figure_region_index': block.get('figure_region_index'),
                    'media_kind': media_kind,
                    'image_width': image_width,
                    'image_height': image_height,
                    'table_columns': block.get('table_columns'),
                    'table_has_header': block.get('table_has_header'),
                    'table_total_rows': block.get('table_total_rows'),
                    'table_row_start': block.get('table_row_start'),
                    'table_row_end': block.get('table_row_end'),
                    'table_markdown': block.get('table_markdown'),
                    'table_cells_json': block.get('table_cells_json'),
                }
            )
        return segments

    def _render_pdf_block_text(
        self,
        block_text: str,
        *,
        role: str,
        page_number: int,
        page_title: str | None,
    ) -> str:
        """把 PDF 块渲染成带语义标签的文本。"""
        role_map = {
            'heading': '标题',
            'table_like': '表格块',
            'figure_caption': '图片说明',
            'figure': '图片区块',
            'body': '正文块',
        }
        lines = [f'PDF 页面：{page_number}']
        if page_title and role != 'heading':
            lines.append(f'页面标题：{page_title}')
        lines.append(f"{role_map.get(role, '正文块')}：")
        lines.append(block_text)
        return '\n'.join(lines).strip()

    def _render_pdf_figure_block_text(
        self,
        image: dict[str, Any],
        *,
        caption_text: str | None,
        nearby_text: str | None,
    ) -> str:
        """把图片区块渲染成可检索文本。"""
        lines = ['图片区块：']
        lines.append(f"区域：{self._serialize_pdf_bbox(image)}")
        if caption_text:
            lines.append(f'图片说明：{caption_text}')
        if nearby_text:
            lines.append(f'关联正文：{nearby_text[:240]}')
        return '\n'.join(lines).strip()

    def _read_pdf_page_with_ocr(self, file_path: Path, *, page_number: int) -> list[dict[str, Any]]:
        """把单页 PDF 渲染成图片后走 OCR 兜底。"""
        try:
            pdf2image = importlib.import_module('pdf2image')
            pil_image_ops = importlib.import_module('PIL.ImageOps')
            pytesseract = importlib.import_module('pytesseract')
        except ModuleNotFoundError:
            return []

        convert_from_path = getattr(pdf2image, 'convert_from_path', None)
        if not callable(convert_from_path):
            return []
        try:
            images = convert_from_path(
                str(file_path),
                first_page=page_number,
                last_page=page_number,
                single_file=True,
            )
        except Exception:
            return []
        if not images:
            return []
        first_image = cast(list[Any], images)[0]
        return self._build_pdf_ocr_segment(first_image, pytesseract, pil_image_ops, page_number=page_number)

    def _build_pdf_ocr_segment(
        self,
        image: Any,
        pytesseract: Any,
        pil_image_ops: Any,
        *,
        page_number: int,
    ) -> list[dict[str, Any]]:
        """复用图片 OCR 链路构造扫描 PDF 页片段。"""
        prepared = self._prepare_image_for_ocr(image, pil_image_ops)
        width = int(getattr(prepared, 'width', getattr(image, 'width', 0)) or 0)
        height = int(getattr(prepared, 'height', getattr(image, 'height', 0)) or 0)
        line_segments = self._extract_image_ocr_lines(prepared, pytesseract)
        if line_segments:
            blocks = self._group_pdf_ocr_lines_into_blocks(line_segments)
            segments = self._build_pdf_segments_from_blocks(
                blocks,
                page_number=page_number,
                pdf_ocr_used=True,
                layout_source='ocr',
                media_kind='pdf_page_image',
                image_width=width,
                image_height=height,
            )
            if segments:
                for segment in segments:
                    segment['ocr_line_count'] = len(line_segments)
                    segment['ocr_line_text'] = '\n'.join(line['text'] for line in line_segments if line.get('text'))
                return segments
        text = str(pytesseract.image_to_string(prepared, lang=self.settings.ocr_languages)).strip()
        if not text:
            return []
        return [
            {
                'text': f'PDF OCR 页：{page_number}\n内容：{text}',
                'page': page_number,
                'section_title': f'page {page_number}',
                'media_kind': 'pdf_page_image',
                'pdf_ocr_used': True,
                'pdf_layout_source': 'ocr_fallback',
                'pdf_page_title': None,
                'pdf_block_role': 'body',
                'pdf_block_index': 1,
                'pdf_block_line_count': 1,
                'image_width': width,
                'image_height': height,
                'ocr_line_count': 1,
                'ocr_line_text': text,
            }
        ]

    def _group_pdf_ocr_lines_into_blocks(self, line_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把扫描 PDF 的 OCR 行组织成页级标题和段落块。"""
        lines = [str(item.get('text') or '').strip() for item in line_segments if str(item.get('text') or '').strip()]
        if not lines:
            return []
        blocks: list[dict[str, Any]] = []
        start_index = 0
        if self._looks_like_pdf_heading(lines[0]):
            blocks.append({'role': 'heading', 'text': lines[0], 'block_index': 1, 'line_count': 1})
            start_index = 1
        paragraph_lines: list[str] = []
        for line in lines[start_index:]:
            paragraph_lines.append(line)
            should_flush = line.endswith(('。', '.', '!', '！', '?', '？', ';', '；')) or len(' '.join(paragraph_lines)) >= 120
            if should_flush:
                role = self._classify_pdf_line_role(' '.join(paragraph_lines))
                if role == 'heading':
                    role = 'body'
                blocks.append(
                    {
                        'role': role,
                        'text': '\n'.join(paragraph_lines).strip(),
                        'line_count': len(paragraph_lines),
                    }
                )
                paragraph_lines = []
        if paragraph_lines:
            role = self._classify_pdf_line_role(' '.join(paragraph_lines))
            if role == 'heading':
                role = 'body'
            blocks.append(
                {
                    'role': role,
                    'text': '\n'.join(paragraph_lines).strip(),
                    'line_count': len(paragraph_lines),
                }
            )
        for index, block in enumerate(blocks, start=1):
            block['block_index'] = index
        return blocks

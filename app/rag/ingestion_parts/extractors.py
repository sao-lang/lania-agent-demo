"""`ingestion.py` 的内容抽取子模块。

集中处理 Office、表格、OCR、媒体与 ZIP 内成员读取，把不同文件格式的文本抽取差异收口到
统一的 segments 结构，供后续切分和索引流程复用。
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
from app.rag.ingestion_parts._typing import IngestionTypingMixin
from app.rag.llamaindex_components import build_embed_model, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import DocumentRecord

class _HTMLTextExtractor(HTMLParser):
    """把 HTML 内容提取为纯文本片段。"""

    def __init__(self) -> None:
        """初始化 HTML 纯文本抽取过程中使用的片段缓冲区。"""
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
        """返回拼接后的纯文本内容。"""
        return "\n".join(self.parts)

class IngestionExtractorMixin(IngestionTypingMixin):
    """封装摄取服务中的多格式文本抽取与结构化片段构建逻辑。"""

    def _read_docx(self, file_path: Path) -> list[dict[str, Any]]:
        """读取 DOCX 并合并非空段落文本。"""
        docx = importlib.import_module('docx')
        DocxDocument = getattr(docx, 'Document')
        document = DocxDocument(str(file_path))
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        if not paragraphs:
            raise ValueError('docx contains no extractable text')
        return [{'text': '\n'.join(paragraphs), 'page': None, 'section_title': None}]

    def _read_pptx(self, file_path: Path) -> list[dict[str, Any]]:
        """读取 PPTX，并尽量区分标题、正文和备注。"""
        pptx = importlib.import_module('pptx')
        Presentation = getattr(pptx, 'Presentation')
        presentation = Presentation(str(file_path))
        segments: list[dict[str, Any]] = []
        for index, slide in enumerate(presentation.slides, start=1):
            title, body_lines = self._extract_pptx_slide_content(slide)
            notes_text = self._extract_pptx_slide_notes(slide)
            rendered_text = self._render_pptx_slide_text(title, body_lines, notes_text, slide_number=index)
            if rendered_text:
                segments.append(
                    {
                        'text': rendered_text,
                        'page': index,
                        'section_title': title[:80] if title else (body_lines[0][:80] if body_lines else None),
                        'slide_number': index,
                        'ppt_title': title,
                        'ppt_body_lines': body_lines,
                        'ppt_body_count': len(body_lines),
                        'ppt_notes': notes_text,
                        'ppt_has_notes': bool(notes_text),
                    }
                )
        if not segments:
            raise ValueError('pptx contains no extractable text')
        return segments

    def _extract_pptx_slide_content(self, slide: Any) -> tuple[str | None, list[str]]:
        """提取单页 PPT 的标题和正文。"""
        title: str | None = None
        body_lines: list[str] = []
        for shape in getattr(slide, 'shapes', []):
            text = getattr(shape, 'text', '')
            normalized = str(text).strip()
            if not normalized:
                continue
            if title is None and self._is_pptx_title_shape(shape):
                title = normalized
                continue
            body_lines.append(normalized)
        if title is None and body_lines:
            title = body_lines.pop(0)
        return title, body_lines

    def _is_pptx_title_shape(self, shape: Any) -> bool:
        """用轻量规则判断 shape 是否更像标题。"""
        if bool(getattr(shape, 'is_title', False)):
            return True
        shape_name = str(getattr(shape, 'name', '')).lower()
        if 'title' in shape_name:
            return True
        placeholder_format = getattr(shape, 'placeholder_format', None)
        placeholder_name = str(getattr(placeholder_format, 'type', '')).lower()
        return 'title' in placeholder_name

    def _extract_pptx_slide_notes(self, slide: Any) -> str | None:
        """提取备注页文本。"""
        notes_text = getattr(slide, 'notes_text', None)
        if isinstance(notes_text, str) and notes_text.strip():
            return notes_text.strip()
        notes_slide = getattr(slide, 'notes_slide', None)
        if notes_slide is None:
            return None
        note_lines: list[str] = []
        for shape in getattr(notes_slide, 'shapes', []):
            text = getattr(shape, 'text', '')
            normalized = str(text).strip()
            if normalized:
                note_lines.append(normalized)
        if not note_lines:
            return None
        return '\n'.join(note_lines)

    def _render_pptx_slide_text(
        self,
        title: str | None,
        body_lines: list[str],
        notes_text: str | None,
        *,
        slide_number: int,
    ) -> str:
        """把 PPT 页面组织成更适合检索的结构化文本。"""
        lines = [f'幻灯片：第 {slide_number} 页']
        if title:
            lines.append(f'标题：{title}')
        if body_lines:
            lines.append('正文：')
            lines.extend(f'- {line}' for line in body_lines)
        if notes_text:
            lines.append('备注：')
            lines.append(notes_text)
        rendered = '\n'.join(lines).strip()
        return rendered if rendered != f'幻灯片：第 {slide_number} 页' else ''

    def _read_legacy_office(self, file_path: Path, target_suffix: str) -> list[dict[str, Any]]:
        """使用本地 LibreOffice 把 doc/ppt 转成新格式后再解析。"""
        converted_path = self._convert_legacy_office(file_path, target_suffix)
        if target_suffix == 'docx':
            return self._read_docx(converted_path)
        if target_suffix == 'pptx':
            return self._read_pptx(converted_path)
        raise ValueError(f'unsupported office conversion target: {target_suffix}')

    def _read_csv(self, file_path: Path) -> list[dict[str, Any]]:
        """读取 CSV 并保留行列顺序。"""
        text = file_path.read_text(encoding='utf-8', errors='ignore')
        reader = csv.reader(io.StringIO(text))
        rows = self._normalize_table_rows([[cell.strip() for cell in row] for row in reader])
        if not rows:
            raise ValueError('csv contains no extractable text')
        return self._build_table_segments(
            title=file_path.stem,
            rows=rows,
            sheet_name=file_path.stem,
        )

    def _read_xlsx(self, file_path: Path) -> list[dict[str, Any]]:
        """读取 XLSX 并按工作表输出文本片段。"""
        openpyxl = importlib.import_module('openpyxl')
        load_workbook = getattr(openpyxl, 'load_workbook')
        workbook = load_workbook(filename=str(file_path), data_only=True, read_only=True)
        segments: list[dict[str, Any]] = []
        for worksheet in workbook.worksheets:
            raw_rows: list[list[str]] = []
            for row in worksheet.iter_rows(values_only=True):
                values = [str(cell).strip() if cell not in (None, '') else '' for cell in row]
                raw_rows.append(values)
            rows = self._normalize_table_rows(raw_rows)
            if not rows:
                continue
            segments.extend(
                self._build_table_segments(
                    title=worksheet.title,
                    rows=rows,
                    sheet_name=worksheet.title,
                )
            )
        if not segments:
            raise ValueError('xlsx contains no extractable text')
        return segments

    def _normalize_table_rows(self, rows: list[list[str]]) -> list[list[str]]:
        """清洗表格空行，并尽量保留列位置。"""
        normalized: list[list[str]] = []
        max_columns = max((len(row) for row in rows), default=0)
        for row in rows:
            values = [str(cell).strip() for cell in row]
            values.extend([''] * max(0, max_columns - len(values)))
            while values and not values[-1]:
                values.pop()
            if any(values):
                normalized.append(values)
        return normalized

    def _build_table_segments(self, title: str, rows: list[list[str]], sheet_name: str) -> list[dict[str, Any]]:
        """把表格内容按表头和行批次组织为更适合检索的片段。"""
        header, data_rows, has_header = self._detect_table_header(rows)
        if not data_rows and rows:
            data_rows = rows if not has_header else [header]
        if not header:
            column_count = max((len(row) for row in data_rows), default=0)
            header = [f'column_{index}' for index in range(1, max(1, column_count) + 1)]
        header = [self._normalize_table_header_name(value, index) for index, value in enumerate(header, start=1)]

        segments: list[dict[str, Any]] = []
        total_rows = len(data_rows)
        if total_rows == 0:
            total_rows = len(rows)
        batches = [data_rows[index : index + self.TABLE_SEGMENT_ROW_BATCH] for index in range(0, len(data_rows), self.TABLE_SEGMENT_ROW_BATCH)]
        if not batches:
            batches = [[]]

        for batch_index, batch_rows in enumerate(batches, start=1):
            row_start = ((batch_index - 1) * self.TABLE_SEGMENT_ROW_BATCH) + 1 if data_rows else 1
            row_end = row_start + max(len(batch_rows) - 1, 0)
            section_title = title if len(batches) == 1 else f'{title} rows {row_start}-{row_end}'
            segments.append(
                {
                    'text': self._render_table_batch_text(title, header, batch_rows, row_start, total_rows, has_header),
                    'page': None,
                    'section_title': section_title,
                    'sheet_name': sheet_name,
                    'row_count': len(batch_rows) if batch_rows else total_rows,
                    'table_total_rows': total_rows,
                    'table_columns': header,
                    'table_has_header': has_header,
                    'table_row_start': row_start,
                    'table_row_end': row_end if batch_rows else total_rows,
                }
            )
        return segments

    def _detect_table_header(self, rows: list[list[str]]) -> tuple[list[str], list[list[str]], bool]:
        """用轻量规则判断首行是否更像表头。"""
        if not rows:
            return [], [], False
        first_row = rows[0]
        next_row = rows[1] if len(rows) > 1 else []
        non_empty = [cell for cell in first_row if cell]
        if len(rows) == 1:
            return first_row, [], True
        if non_empty and len(non_empty) == len(set(item.lower() for item in non_empty)) and self._looks_like_header_row(first_row, next_row):
            return first_row, rows[1:], True
        return first_row, rows, False

    def _looks_like_header_row(self, first_row: list[str], next_row: list[str]) -> bool:
        """判断一行是否更像列头而不是数据。"""
        first_non_empty = [cell for cell in first_row if cell]
        if len(first_non_empty) < 2:
            return False
        alpha_like = sum(1 for cell in first_non_empty if re.search(r'[A-Za-z一-鿿]', cell))
        numeric_like = sum(1 for cell in first_non_empty if re.fullmatch(r'[-+]?\d+(?:\.\d+)?%?', cell))
        next_numeric_like = sum(1 for cell in next_row if cell and re.fullmatch(r'[-+]?\d+(?:\.\d+)?%?', cell))
        return (
            alpha_like >= max(1, len(first_non_empty) // 2)
            and numeric_like == 0
            and next_numeric_like >= 1
        )

    def _normalize_table_header_name(self, value: str, index: int) -> str:
        """为缺失或噪声列头生成稳定名称。"""
        normalized = re.sub(r'\s+', ' ', str(value).strip())
        return normalized or f'column_{index}'

    def _render_table_batch_text(
        self,
        title: str,
        header: list[str],
        rows: list[list[str]],
        row_start: int,
        total_rows: int,
        has_header: bool,
    ) -> str:
        """把表格批次渲染为列头清晰、适合检索的文本。"""
        lines = [
            f'表格：{title}',
            f"列头：{' | '.join(header)}",
            f'数据行数：{total_rows}',
            f"表头识别：{'yes' if has_header else 'no'}",
        ]
        if rows:
            lines.append(f'当前行范围：{row_start}-{row_start + len(rows) - 1}')
        else:
            lines.append('当前行范围：1-0')
        for offset, row in enumerate(rows, start=row_start):
            cells = list(row) + [''] * max(0, len(header) - len(row))
            pairs = [f'{column}={value}' for column, value in zip(header, cells, strict=False) if value]
            if not pairs:
                continue
            lines.append(f"第{offset}行：{'; '.join(pairs)}")
        return '\n'.join(lines).strip()

    def _read_text_file(self, file_path: Path, suffix: str) -> list[dict[str, Any]]:
        """读取纯文本或代码文件。"""
        text = file_path.read_text(encoding='utf-8', errors='ignore')
        normalized = text.strip()
        if not normalized:
            raise ValueError(f'{suffix} contains no extractable text')
        title = file_path.stem if suffix != 'txt' else None
        return [{'text': text, 'page': None, 'section_title': title, 'code_language': suffix if suffix != 'txt' else None}]

    def _read_html(self, file_path: Path) -> str:
        """读取 HTML 并提取可见文本。"""
        parser = _HTMLTextExtractor()
        parser.feed(file_path.read_text(encoding='utf-8', errors='ignore'))
        text = parser.get_text()
        if not text.strip():
            raise ValueError('html contains no extractable text')
        return text

    def _read_markdown(self, file_path: Path) -> list[dict[str, Any]]:
        """按 Markdown 标题切分文本片段。"""
        text = file_path.read_text(encoding='utf-8', errors='ignore')
        lines = text.splitlines()
        segments: list[dict[str, Any]] = []
        current_title: str | None = None
        current_level: int | None = None
        current_path: str | None = None
        current_lines: list[str] = []
        heading_stack: list[tuple[int, str]] = []

        for line in lines:
            heading = re.match(r'^\s{0,3}#{1,6}\s+(.*)$', line)
            if heading:
                if current_lines:
                    segments.append(
                        {
                            'text': '\n'.join(current_lines).strip(),
                            'page': None,
                            'section_title': current_title,
                            'section_level': current_level,
                            'hierarchy_path': current_path,
                        }
                    )
                    current_lines = []
                level = len(line.lstrip().split(' ')[0])
                title = heading.group(1).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                current_title = title
                current_level = level
                current_path = ' > '.join(item[1] for item in heading_stack)
                continue
            current_lines.append(line)

        if current_lines:
            segments.append(
                {
                    'text': '\n'.join(current_lines).strip(),
                    'page': None,
                    'section_title': current_title,
                    'section_level': current_level,
                    'hierarchy_path': current_path,
                }
            )

        normalized = [segment for segment in segments if segment['text']]
        if not normalized:
            raise ValueError('markdown contains no extractable text')
        return normalized

    def _read_image(self, file_path: Path, suffix: str) -> list[dict[str, Any]]:
        """通过本地 OCR 提取图片文本。"""
        try:
            pil_image = importlib.import_module('PIL.Image')
            pil_image_ops = importlib.import_module('PIL.ImageOps')
            pytesseract = importlib.import_module('pytesseract')
        except ModuleNotFoundError as exc:
            raise ValueError(
                f'image OCR dependency missing for {suffix}; install Pillow and pytesseract, and ensure local tesseract is available'
            ) from exc

        Image = getattr(pil_image, 'open')
        with Image(str(file_path)) as image:
            prepared = self._prepare_image_for_ocr(image, pil_image_ops)
            width = int(getattr(prepared, 'width', getattr(image, 'width', 0)) or 0)
            height = int(getattr(prepared, 'height', getattr(image, 'height', 0)) or 0)
            line_segments = self._extract_image_ocr_lines(prepared, pytesseract)
            if line_segments:
                return [
                    {
                        'text': self._render_image_ocr_line_text(file_path.stem, line['text'], line['line_index']),
                        'page': None,
                        'section_title': f"{file_path.stem} line {line['line_index']}",
                        'media_kind': 'image',
                        'image_width': width,
                        'image_height': height,
                        'ocr_line_index': line['line_index'],
                        'ocr_line_confidence': line['confidence'],
                        'ocr_line_text': line['text'],
                    }
                    for line in line_segments
                ]
            text = str(pytesseract.image_to_string(prepared, lang=self.settings.ocr_languages)).strip()
        if not text:
            raise ValueError('image contains no extractable text')
        return [
            {
                'text': self._render_image_ocr_line_text(file_path.stem, text, 1),
                'page': None,
                'section_title': file_path.stem,
                'media_kind': 'image',
                'image_width': width,
                'image_height': height,
                'ocr_line_index': 1,
                'ocr_line_confidence': None,
                'ocr_line_text': text,
            }
        ]

    def _prepare_image_for_ocr(self, image: Any, pil_image_ops: Any) -> Any:
        """对图片做轻量预处理，提升 OCR 稳定性。"""
        prepared = image
        exif_transpose = getattr(pil_image_ops, 'exif_transpose', None)
        if callable(exif_transpose):
            prepared = exif_transpose(prepared)
        if hasattr(prepared, 'convert'):
            prepared = cast(Any, prepared).convert('L')
        autocontrast = getattr(pil_image_ops, 'autocontrast', None)
        if callable(autocontrast):
            prepared = autocontrast(prepared)
        if hasattr(prepared, 'point'):
            prepared = cast(Any, prepared).point(lambda value: 255 if value > 180 else 0)
        return prepared

    def _extract_image_ocr_lines(self, image: Any, pytesseract: Any) -> list[dict[str, Any]]:
        """优先用 OCR 行级结果构造更细粒度的图片文本片段。"""
        image_to_data = getattr(pytesseract, 'image_to_data', None)
        if not callable(image_to_data):
            return []
        output_namespace = getattr(pytesseract, 'Output', None)
        output_dict = getattr(output_namespace, 'DICT', None)
        kwargs: dict[str, Any] = {'lang': self.settings.ocr_languages}
        if output_dict is not None:
            kwargs['output_type'] = output_dict
        try:
            data = image_to_data(image, **kwargs)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []

        texts = list(data.get('text') or [])
        line_map: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        for index, raw_text in enumerate(texts):
            text = str(raw_text or '').strip()
            if not text:
                continue
            key = (
                self._safe_int(self._lookup_ocr_value(data, 'block_num', index)) or 0,
                self._safe_int(self._lookup_ocr_value(data, 'par_num', index)) or 0,
                self._safe_int(self._lookup_ocr_value(data, 'line_num', index)) or index + 1,
            )
            entry = line_map.setdefault(key, {'parts': [], 'confidences': []})
            entry['parts'].append(text)
            confidence = self._safe_float(self._lookup_ocr_value(data, 'conf', index))
            if confidence is not None and confidence >= 0:
                entry['confidences'].append(confidence)

        segments: list[dict[str, Any]] = []
        for line_index, (_, payload) in enumerate(sorted(line_map.items()), start=1):
            line_text = ' '.join(payload['parts']).strip()
            if not line_text:
                continue
            confidences = payload['confidences']
            avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None
            segments.append({'line_index': line_index, 'text': line_text, 'confidence': avg_confidence})
        return segments

    def _lookup_ocr_value(self, data: dict[str, Any], key: str, index: int) -> Any:
        """安全读取 OCR 结构化输出中的单元值。"""
        values = data.get(key) or []
        if not isinstance(values, list) or index >= len(values):
            return None
        return values[index]

    def _render_image_ocr_line_text(self, title: str, text: str, line_index: int) -> str:
        """把图片 OCR 行结果渲染成更适合检索的文本。"""
        return f'图片：{title}\nOCR 行：{line_index}\n内容：{text}'.strip()

    def _read_media(self, file_path: Path, suffix: str) -> list[dict[str, Any]]:
        """使用本地 Whisper 模型转写音频或视频。"""
        try:
            whisper = importlib.import_module('whisper')
        except ModuleNotFoundError as exc:
            raise ValueError(
                'media transcription dependency missing; install openai-whisper and ensure ffmpeg is available locally'
            ) from exc

        try:
            load_model = getattr(whisper, 'load_model')
            model = load_model(self.settings.transcription_model)
            result = model.transcribe(str(file_path), fp16=False)
        except Exception as exc:
            raise ValueError(
                'media transcription dependency missing or unavailable; install openai-whisper and ensure ffmpeg is available locally'
            ) from exc
        media_kind = 'audio' if suffix in self.AUDIO_TYPES else 'video'
        segments = self._build_media_transcript_segments(result, media_kind=media_kind, title=file_path.stem)
        if not segments:
            raise ValueError(f'{suffix} contains no extractable transcript')
        return segments

    def _build_media_transcript_segments(
        self,
        transcript_result: dict[str, Any],
        *,
        media_kind: str,
        title: str,
    ) -> list[dict[str, Any]]:
        """把 Whisper 转写结果规范化为按时间片分段的结构。"""
        raw_segments = transcript_result.get('segments') or []
        normalized_segments: list[dict[str, Any]] = []

        for index, raw_segment in enumerate(raw_segments, start=1):
            if not isinstance(raw_segment, dict):
                continue
            text = str(raw_segment.get('text') or '').strip()
            if not text:
                continue
            start_seconds = self._safe_float(raw_segment.get('start'))
            end_seconds = self._safe_float(raw_segment.get('end'))
            normalized_segments.append(
                {
                    'text': self._render_media_segment_text(text, start_seconds, end_seconds, index=index),
                    'page': None,
                    'section_title': self._build_media_segment_title(title, start_seconds, end_seconds, index=index),
                    'media_kind': media_kind,
                    'media_segment_index': index,
                    'transcript_start_seconds': start_seconds,
                    'transcript_end_seconds': end_seconds,
                    'transcript_timecode': self._build_media_timecode(start_seconds, end_seconds),
                    'transcript_text': text,
                }
            )

        if normalized_segments:
            return normalized_segments

        text = str(transcript_result.get('text') or '').strip()
        if not text:
            return []
        return [
            {
                'text': self._render_media_segment_text(text, None, None, index=1),
                'page': None,
                'section_title': title,
                'media_kind': media_kind,
                'media_segment_index': 1,
                'transcript_start_seconds': None,
                'transcript_end_seconds': None,
                'transcript_timecode': None,
                'transcript_text': text,
            }
        ]

    def _render_media_segment_text(
        self,
        text: str,
        start_seconds: float | None,
        end_seconds: float | None,
        *,
        index: int,
    ) -> str:
        """把转写片段渲染为带时间信息的文本。"""
        lines = [f'转写片段：{index}']
        timecode = self._build_media_timecode(start_seconds, end_seconds)
        if timecode:
            lines.append(f'时间范围：{timecode}')
        lines.append(f'内容：{text}')
        return '\n'.join(lines).strip()

    def _build_media_segment_title(
        self,
        title: str,
        start_seconds: float | None,
        end_seconds: float | None,
        *,
        index: int,
    ) -> str:
        """为转写片段生成可读标题。"""
        timecode = self._build_media_timecode(start_seconds, end_seconds)
        if timecode:
            return f'{title} [{timecode}]'
        return f'{title} segment {index}'

    def _build_media_timecode(self, start_seconds: float | None, end_seconds: float | None) -> str | None:
        """把秒数转换成可展示的时间范围。"""
        if start_seconds is None and end_seconds is None:
            return None
        start = self._format_media_seconds(start_seconds or 0.0)
        end = self._format_media_seconds(end_seconds or start_seconds or 0.0)
        return f'{start}-{end}'

    def _format_media_seconds(self, value: float) -> str:
        """格式化媒体时间戳。"""
        total_seconds = max(0, int(round(value)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
        return f'{minutes:02d}:{seconds:02d}'

    def _read_zip(self, file_path: Path) -> list[dict[str, Any]]:
        """安全读取 ZIP，并聚合内部可解析文件的文本内容。"""
        segments: list[dict[str, Any]] = []

        with tempfile.TemporaryDirectory(prefix='rag-zip-') as tmp_dir_name:
            extracted_root = Path(tmp_dir_name) / (file_path.stem or 'archive')
            extracted_members = self._extract_archive_members(file_path, extracted_root)
            for member_name, target_path, suffix in extracted_members:
                try:
                    child_segments = self._extract_segments(target_path)
                except ValueError:
                    continue
                member_path = Path(member_name)
                for child_segment in child_segments:
                    text = str(child_segment.get('text') or '').strip()
                    if not text:
                        continue
                    payload = dict(child_segment)
                    payload['text'] = f"[ZIP member: {member_name}]\n{text}"
                    payload['archive_member_path'] = member_name
                    payload['archive_member_type'] = suffix
                    payload['section_title'] = str(payload.get('section_title') or member_path.stem).strip() or member_path.stem
                    segments.append(payload)

        if not segments:
            raise ValueError('zip contains no supported extractable files')
        return segments

    def _convert_legacy_office(self, file_path: Path, target_suffix: str) -> Path:
        """调用本地 LibreOffice 转换老 Office 文档。"""
        cache_dir = self._converted_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._prune_converted_cache(cache_dir)
        cache_path = self._legacy_conversion_cache_path(file_path, target_suffix)
        if cache_path.exists():
            cache_path.touch()
            return cache_path

        soffice = self._resolve_office_converter_command()
        if soffice is None:
            raise ValueError(
                f'legacy office format requires LibreOffice conversion; install LibreOffice or set OFFICE_CONVERTER_COMMAND, then retry converting to {target_suffix}'
            )

        with tempfile.TemporaryDirectory(prefix='rag-office-convert-') as tmp_dir_name:
            output_dir = Path(tmp_dir_name)
            command = [
                soffice,
                '--headless',
                '--convert-to',
                target_suffix,
                '--outdir',
                str(output_dir),
                str(file_path),
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(1, self.settings.office_conversion_timeout_seconds),
            )
            if completed.returncode != 0:
                stderr = (completed.stderr or completed.stdout or '').strip()
                reason = stderr or f'converter exited with code {completed.returncode}'
                raise ValueError(f'legacy office conversion failed for {file_path.suffix.lower()}: {reason}')

            converted_path = output_dir / f'{file_path.stem}.{target_suffix}'
            if not converted_path.exists():
                matches = sorted(output_dir.glob(f'*.{target_suffix}'))
                if not matches:
                    raise ValueError(f'legacy office conversion produced no {target_suffix} output')
                converted_path = matches[0]
            shutil.copy2(converted_path, cache_path)
            self._prune_converted_cache(cache_dir)
            return cache_path

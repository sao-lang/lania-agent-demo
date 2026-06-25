"""`ingestion.py` 的导入校验与落盘子模块。

把目录扫描、导入目标计算、压缩包安全解压、类型检测和持久化等辅助能力收拢，
避免主文件尾部堆积大量与摄取主链路正交的工具方法。
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
from app.rag.ingestion_parts._typing import IngestionTypingMixin
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import DocumentRecord

class IngestionImportMixin(IngestionTypingMixin):
    """封装摄取服务中的导入校验、落盘与压缩包处理逻辑。"""

    def _find_document_by_checksum(self, collection_name: str, checksum: str) -> DocumentRecord | None:
        """按内容摘要查找集合内是否已存在相同文档。"""
        for document in self.state.documents.values():
            if document['collection_name'] == collection_name and document.get('checksum') == checksum:
                return document
        return None

    def _build_destination_path(self, destination_dir: Path, filename: str) -> Path:
        """为导入文件分配不冲突的目标路径。"""
        candidate = destination_dir / filename
        if not candidate.exists():
            return candidate

        stem = Path(filename).stem
        suffix = Path(filename).suffix
        return destination_dir / f'{stem}-{uuid4().hex[:6]}{suffix}'

    def detect_file_type(self, file_path: Path) -> str:
        """识别文件扩展名，兼容 Dockerfile 这类无后缀文本文件。"""
        suffix = file_path.suffix.lower().lstrip('.')
        if suffix:
            return suffix
        return file_path.name.lower()

    def _resolve_office_converter_command(self) -> str | None:
        """解析本地 LibreOffice 转换命令路径。"""
        configured = str(self.settings.office_converter_command).strip()
        candidates = [configured] if configured else []
        if '/Applications/LibreOffice.app/Contents/MacOS/soffice' not in candidates:
            candidates.append('/Applications/LibreOffice.app/Contents/MacOS/soffice')
        for candidate in candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate)
            if candidate_path.is_absolute():
                if candidate_path.exists():
                    return str(candidate_path)
                continue
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None

    def _converted_cache_dir(self) -> Path:
        """返回老 Office 转换缓存目录。"""
        return self.settings.uploads_dir / '.converted'

    def _legacy_conversion_cache_path(self, file_path: Path, target_suffix: str) -> Path:
        """基于源文件内容生成稳定缓存路径，避免重复转换。"""
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        stem = re.sub(r'[^0-9A-Za-z._-]+', '-', file_path.stem).strip('-') or 'document'
        return self._converted_cache_dir() / f'{stem}-{digest[:16]}.{target_suffix}'

    def _prune_converted_cache(self, cache_dir: Path) -> None:
        """按过期时间和最大文件数裁剪历史转换缓存。"""
        if not cache_dir.exists():
            return
        ttl_seconds = max(0, int(self.settings.converted_cache_ttl_seconds))
        max_files = max(1, int(self.settings.converted_cache_max_files))
        now_ts = datetime.now(timezone.utc).timestamp()
        entries = [item for item in cache_dir.iterdir() if item.is_file()]
        deleted_count = 0

        if ttl_seconds > 0:
            for item in entries:
                try:
                    age_seconds = max(0.0, now_ts - item.stat().st_mtime)
                except FileNotFoundError:
                    continue
                if age_seconds > ttl_seconds:
                    if item.exists():
                        item.unlink(missing_ok=True)
                        deleted_count += 1
            entries = [item for item in cache_dir.iterdir() if item.is_file()]

        if len(entries) > max_files:
            ordered = sorted(entries, key=lambda item: item.stat().st_mtime, reverse=True)
            for stale in ordered[max_files:]:
                if stale.exists():
                    stale.unlink(missing_ok=True)
                    deleted_count += 1

        self._converted_cache_prune_runs += 1
        self._converted_cache_deleted_files += deleted_count
        self._converted_cache_last_pruned_at = datetime.now(timezone.utc)

    def get_conversion_cache_status(self) -> dict[str, Any]:
        """返回老 Office 转换缓存的运行时状态。"""
        cache_dir = self._converted_cache_dir()
        files = [item for item in cache_dir.iterdir() if item.is_file()] if cache_dir.exists() else []
        total_bytes = 0
        for item in files:
            try:
                total_bytes += int(item.stat().st_size)
            except FileNotFoundError:
                continue
        return {
            'enabled_for_legacy_formats': True,
            'cache_dir': str(cache_dir),
            'file_count': len(files),
            'total_bytes': total_bytes,
            'max_files': int(self.settings.converted_cache_max_files),
            'ttl_seconds': int(self.settings.converted_cache_ttl_seconds),
            'prune_runs': self._converted_cache_prune_runs,
            'deleted_files': self._converted_cache_deleted_files,
            'last_pruned_at': self._to_iso(self._converted_cache_last_pruned_at),
        }

    def _resolve_import_target_path(
        self,
        destination_dir: Path,
        source_path: Path,
        destination_subpath: Path | None = None,
    ) -> Path:
        """解析导入文件最终落盘位置，支持保留 ZIP 内层级。"""
        if destination_subpath is None:
            return self._build_destination_path(destination_dir, source_path.name)

        normalized_parts = [part for part in destination_subpath.parts if part not in {'', '.'}]
        if not normalized_parts or any(part == '..' for part in normalized_parts):
            raise ValueError(f'invalid destination_subpath: {destination_subpath}')
        requested_path = destination_dir.joinpath(*normalized_parts)
        requested_path.parent.mkdir(parents=True, exist_ok=True)
        if requested_path.exists() and source_path.resolve() != requested_path.resolve():
            return self._build_destination_path(requested_path.parent, requested_path.name)
        return requested_path

    def _extract_archive_members(self, archive_path: Path, destination_dir: Path) -> list[tuple[str, Path, str]]:
        """把 ZIP 内可支持的成员安全解压到目标目录。"""
        destination_root = destination_dir.resolve()
        destination_root.mkdir(parents=True, exist_ok=True)
        extracted: list[tuple[str, Path, str]] = []
        member_count = 0
        total_size = 0

        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member_count += 1
                total_size += max(0, int(info.file_size))
                if member_count > self.settings.archive_max_member_count:
                    raise ValueError('zip exceeds archive_max_member_count limit')
                if total_size > self.settings.archive_max_total_bytes:
                    raise ValueError('zip exceeds archive_max_total_bytes limit')

                member_path = Path(info.filename)
                normalized_parts = [part for part in member_path.parts if part not in {'', '.'}]
                if not normalized_parts or any(part == '..' for part in normalized_parts):
                    raise ValueError(f'unsafe zip member path: {info.filename}')

                # ZIP 导入必须阻断路径穿越，只允许落在集合目录内部的规范化路径。
                normalized_member_path = Path(*normalized_parts)
                file_type = self.detect_file_type(normalized_member_path)
                if file_type not in self.SUPPORTED_TYPES or file_type == 'zip':
                    continue

                target_path = (destination_root / normalized_member_path).resolve()
                if target_path != destination_root and destination_root not in target_path.parents:
                    raise ValueError(f'zip member escapes extraction directory: {info.filename}')

                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target_path.open('wb') as target:
                    shutil.copyfileobj(source, target)
                extracted.append((info.filename, target_path, file_type))
        return extracted

    def _to_iso(self, value: Any) -> str | None:
        """把日期时间等值规范化为可序列化字符串。"""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def validate_import_candidate(
        self,
        file_name: str,
        *,
        file_type: str,
        file_size: int,
        content_type: str | None = None,
    ) -> dict[str, Any] | None:
        """校验导入候选文件的大小、扩展名和 MIME。"""
        normalized_type = file_type.lower().strip()
        if normalized_type not in self.SUPPORTED_TYPES:
            return {
                'file_name': file_name,
                'reason': f'unsupported file type: {normalized_type}',
                'code': 'unsupported_file_type',
                'stage': 'validation',
                'file_type': normalized_type,
            }
        if file_size > max(1, self.settings.max_import_file_bytes):
            return {
                'file_name': file_name,
                'reason': f'file exceeds max_import_file_bytes ({self.settings.max_import_file_bytes} bytes)',
                'code': 'file_too_large',
                'stage': 'validation',
                'file_type': normalized_type,
            }
        if content_type:
            normalized_content_type = content_type.split(';', 1)[0].strip().lower()
            allowed_types = self.MIME_OVERRIDES.get(normalized_type)
            if allowed_types and normalized_content_type not in allowed_types:
                return {
                    'file_name': file_name,
                    'reason': f'content type {normalized_content_type} does not match file type {normalized_type}',
                    'code': 'content_type_mismatch',
                    'stage': 'validation',
                    'file_type': normalized_type,
                }
        return None

    def build_import_failure(self, file_name: str, exc: Exception, file_type: str | None = None) -> dict[str, Any]:
        """把底层导入异常归一化为结构化失败项。"""
        reason = str(exc)
        normalized_type = file_type.lower().strip() if file_type else None
        code = 'import_failed'
        stage = 'import'
        lowered = reason.lower()
        if 'unsupported file type' in lowered:
            code = 'unsupported_file_type'
            stage = 'validation'
        elif 'file exceeds max_import_file_bytes' in lowered:
            code = 'file_too_large'
            stage = 'validation'
        elif 'content type' in lowered and 'does not match file type' in lowered:
            code = 'content_type_mismatch'
            stage = 'validation'
        elif 'requires libreoffice conversion' in lowered:
            code = 'office_converter_missing'
            stage = 'conversion'
        elif 'legacy office conversion failed' in lowered or 'conversion produced no' in lowered:
            code = 'office_conversion_failed'
            stage = 'conversion'
        elif 'dependency missing' in lowered:
            code = 'parser_dependency_missing'
            stage = 'parse'
        elif 'contains no extractable' in lowered or 'contains no supported extractable files' in lowered:
            code = 'no_extractable_content'
            stage = 'parse'
        elif 'unsafe zip member path' in lowered or 'zip member escapes extraction directory' in lowered:
            code = 'unsafe_archive_member'
            stage = 'archive'
        elif 'zip exceeds' in lowered:
            code = 'archive_limits_exceeded'
            stage = 'archive'
        elif 'document not found' in lowered or 'file not found' in lowered:
            code = 'source_not_found'
            stage = 'import'
        return {
            'file_name': file_name,
            'reason': reason,
            'code': code,
            'stage': stage,
            'file_type': normalized_type,
        }

    def _persist_document(self, record: DocumentRecord) -> None:
        """将文档记录同步到持久化层。"""
        if self.persistence is not None:
            self.persistence.upsert_document(record)

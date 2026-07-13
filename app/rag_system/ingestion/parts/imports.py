"""RAG 系统文档导入子模块。

负责目录扫描、checksum 去重、类型检测、ZIP 安全解压、Office 转换缓存。
与主应用的 `app/rag/ingestion_parts/imports.py` 功能一致。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.rag_system.ingestion.parts._typing import RagIngestionTypingMixin


class RagIngestionImportMixin(RagIngestionTypingMixin):
    """目录扫描、checksum 去重、类型检测、ZIP 安全解压、Office 转换缓存。"""

    SUPPORTED_TYPES: set[str] = set()

    def _detect_file_type(self, file_path: Path) -> str:
        ext = file_path.suffix.lstrip('.').lower()
        if ext in self.TEXT_LIKE_TYPES:
            return 'text'
        if ext in self.OFFICE_TYPES:
            return 'office'
        if ext in self.IMAGE_TYPES:
            return 'image'
        if ext in self.AUDIO_TYPES:
            return 'audio'
        if ext in self.VIDEO_TYPES:
            return 'video'
        if ext == 'zip':
            return 'archive'
        return 'unknown'

    def _compute_checksum(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()

    def _is_duplicate(self, checksum: str) -> bool:
        for doc in self.state.documents.values():
            if doc.get('checksum') == checksum:
                return True
        return False

    def _safe_extract_zip(self, zip_path: Path, extract_dir: Path) -> list[Path]:
        """安全解压 ZIP，防止路径穿越。"""
        extracted: list[Path] = []
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                safe_path = (extract_dir / info.filename).resolve()
                if not str(safe_path).startswith(str(extract_dir.resolve())):
                    continue  # 路径穿越防护
                zf.extract(info, extract_dir)
                extracted.append(safe_path)
        return extracted

    def _convert_with_cache(self, file_path: Path, target_format: str = 'txt') -> str | None:
        """带缓存的 Office 转换。"""
        from app.rag_system.ingestion.parts.extractors import RagIngestionExtractorMixin
        cache_dir = self.settings.resolved_data_dir / '.converted_cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = f'{file_path.stem}_{target_format}'
        cache_file = cache_dir / f'{cache_key}.txt'
        if cache_file.exists():
            age = datetime.now(timezone.utc).timestamp() - cache_file.stat().st_mtime
            if age < self.settings.office_conversion_timeout_seconds * 10:
                return cache_file.read_text(encoding='utf-8', errors='replace')
        result = RagIngestionExtractorMixin._convert_with_libreoffice(self, file_path, target_format)
        if result:
            cache_file.write_text(result, encoding='utf-8')
            self._prune_converted_cache(cache_dir)
        return result

    def _prune_converted_cache(self, cache_dir: Path) -> None:
        """修剪转换缓存。"""
        max_files = 64
        files = sorted(cache_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[max_files:]:
            f.unlink(missing_ok=True)

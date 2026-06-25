"""`ingestion_parts` 的静态类型辅助定义。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any


class IngestionTypingMixin:
    """给拆分 mixin 提供宿主类成员的静态类型兜底。"""

    if TYPE_CHECKING:
        TABLE_SEGMENT_ROW_BATCH: int
        AUDIO_TYPES: set[str]
        SUPPORTED_TYPES: set[str]
        MIME_OVERRIDES: dict[str, tuple[str, ...] | set[str]]
        _converted_cache_prune_runs: int
        _converted_cache_deleted_files: int
        _converted_cache_last_pruned_at: datetime | None

        def __getattr__(self, name: str) -> Any: ...

"""graph_parts 的静态类型辅助定义。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class RagGraphServiceTypingMixin:
    """给拆分 mixin 提供宿主类成员的静态类型兜底。"""
    if TYPE_CHECKING:
        ENTITY_STOPWORDS: set[str]
        RELATION_KEYWORDS: dict[str, tuple[str, ...]]
        RELATION_PATTERNS: list[tuple[str, Any]]
        llm: Any | None
        state: Any
        persistence: Any
        trace: Any

        def __getattr__(self, name: str) -> Any: ...

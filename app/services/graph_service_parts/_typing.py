"""`graph_service_parts` 的静态类型辅助定义。"""

from typing import TYPE_CHECKING, Any


class GraphServiceTypingMixin:
    """给拆分 mixin 提供宿主类成员的静态类型兜底。"""

    if TYPE_CHECKING:
        ENTITY_STOPWORDS: set[str]
        RELATION_KEYWORDS: dict[str, tuple[str, ...]]
        RELATION_PATTERNS: list[tuple[str, Any]]

        def __getattr__(self, name: str) -> Any: ...

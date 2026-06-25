"""`retrieval_parts` 的静态类型辅助定义。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class RetrievalTypingMixin:
    """给拆分 mixin 提供宿主类成员的静态类型兜底。"""

    if TYPE_CHECKING:
        cross_encoder: Any | None
        cross_encoder_error: str | None
        cross_encoder_load_attempted: bool

        def __getattr__(self, name: str) -> Any: ...

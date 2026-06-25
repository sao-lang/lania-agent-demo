"""`eval_service_parts` 的静态类型辅助定义。"""

from typing import TYPE_CHECKING, Any


class EvalServiceTypingMixin:
    """给拆分 mixin 提供宿主类成员的静态类型兜底。"""

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any: ...

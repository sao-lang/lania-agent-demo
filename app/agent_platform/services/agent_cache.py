"""Agent Session 级工具调用缓存。

作用范围仅限当前 session。
不是缓存 LLM 输出，而是缓存工具调用结果，
避免同一 session 中重复调用相同工具+相同参数。
session 结束后自动释放。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class AgentSessionCache:
    """Session 级工具调用缓存。"""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}  # session_id → key → value

    def _key(self, tool_name: str, args: dict) -> str:
        """生成缓存键。"""
        serialized = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return f"{tool_name}:{hashlib.md5(serialized.encode()).hexdigest()}"

    async def get(self, session_id: str, tool_name: str, args: dict) -> Any | None:
        """获取缓存结果。"""
        return self._cache.get(session_id, {}).get(self._key(tool_name, args))

    async def set(self, session_id: str, tool_name: str, args: dict, result: Any) -> None:
        """设置缓存结果。"""
        self._cache.setdefault(session_id, {})[self._key(tool_name, args)] = result

    def clear_session(self, session_id: str) -> None:
        """清除某 session 的所有缓存。"""
        self._cache.pop(session_id, None)

    def clear_all(self) -> None:
        """清除所有缓存。"""
        self._cache.clear()

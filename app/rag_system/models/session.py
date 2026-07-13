"""RAG 系统会话模型模块。

定义 RAG 内部使用的会话模型，与主应用的会话模型解耦。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SessionMessage(BaseModel):
    """会话中的单条消息。"""

    role: str  # user / assistant
    content: str
    timestamp: datetime | None = None


class SessionDetail(BaseModel):
    """RAG 系统内部的会话记录。"""

    session_id: str
    messages: list[SessionMessage] = Field(default_factory=list)
    summary: str | None = None
    collection_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSummaryItem(BaseModel):
    """会话摘要中的单条记录。"""

    session_id: str
    summary: str
    message_count: int
    created_at: str | None = None
    updated_at: str | None = None


class SessionSummaryResponse(BaseModel):
    """会话摘要列表响应。"""

    sessions: list[SessionSummaryItem]
    total: int = 0

"""会话管理模块。

管理 Agent 对话会话，包括创建、保存、查询和压缩历史。
类似 Copilot CLI 的会话概念，但存储在后端。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Message(BaseModel):
    """单条对话消息。"""

    role: str  # user | assistant | system
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """Agent 对话会话。"""

    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    history: list[Message] = Field(default_factory=list)
    mode: str = "chat"
    capability: str | None = None
    collection_name: str = "default"
    agent_name: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class SessionManager:
    """会话管理器。

    管理 Agent 对话的生命周期。
    当前使用内存存储，可扩展为 SQLite 持久化。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def get_or_create(self, session_id: str | None = None) -> Session:
        """获取或创建会话。"""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]

        session = Session(id=session_id or str(uuid4()))
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def save(self, session: Session) -> None:
        """保存会话状态。"""
        session.updated_at = datetime.now(timezone.utc)
        self._sessions[session.id] = session

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def add_message(self, session_id: str, message: Message) -> None:
        """向会话追加消息。"""
        session = await self.get_or_create(session_id)
        session.history.append(message)
        await self.save(session)

    async def clear_history(self, session_id: str) -> None:
        """清除会话历史。"""
        session = await self.get(session_id)
        if session:
            session.history = []
            await self.save(session)

    async def list_sessions(self) -> list[Session]:
        """列出所有会话。"""
        return list(self._sessions.values())

    async def set_mode(self, session_id: str, mode: str) -> None:
        """切换会话的执行模式。"""
        session = await self.get_or_create(session_id)
        session.mode = mode
        await self.save(session)

"""会话管理模块。

管理 Agent 对话会话，包括创建、保存、查询和压缩历史。
类似 Copilot CLI 的会话概念，但存储在后端。

Phase 2 重构：接入 InMemoryState + SQLiteStateStore 双写持久化，
在消息交互时写入 MemoryRecord(scope='session')。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.agents.memory import TaskMemory
    from app.services.sqlite_store import SQLiteStateStore
    from app.services.state import InMemoryState


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
    Phase 2 重构后接入 InMemoryState + SQLiteStateStore 双写持久化，
    并在消息交互时通过 TaskMemory 写入 MemoryRecord(scope='session')。
    """

    def __init__(
        self,
        state: InMemoryState | None = None,
        persistence: SQLiteStateStore | None = None,
        task_memory: TaskMemory | None = None,
        max_history: int = 100,
    ) -> None:
        """初始化会话管理器。

        Args:
            state: 内存态业务数据，用于会话记录的快速访问。
            persistence: SQLite 持久化存储，用于会话记录的持久化。
            task_memory: 任务记忆系统，用于在消息交互时写入 MemoryRecord。
            max_history: 单个会话最多保留的消息条数。
        """
        self._state = state
        self._persistence = persistence
        self._task_memory = task_memory
        self._max_history = max_history
        self._sessions: dict[str, Session] = {}

    async def get_or_create(self, session_id: str | None = None) -> Session:
        """获取或创建会话。

        查找顺序：内存缓存 → SQLite 持久化 → 新建。
        """
        if session_id:
            if session_id in self._sessions:
                return self._sessions[session_id]
            if self._persistence:
                raw = self._persistence.get_session(session_id)
                if raw:
                    session = self._deserialize(raw)
                    self._sessions[session_id] = session
                    return session

        session = Session(id=session_id or str(uuid4()))
        self._sessions[session.id] = session
        await self._persist(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        """获取指定会话。"""
        if session_id in self._sessions:
            return self._sessions[session_id]
        if self._persistence:
            raw = self._persistence.get_session(session_id)
            if raw:
                session = self._deserialize(raw)
                self._sessions[session_id] = session
                return session
        return None

    async def save(self, session: Session) -> None:
        """保存会话状态。

        双写：内存缓存 + SQLite 持久化。
        """
        session.updated_at = datetime.now(timezone.utc)
        self._sessions[session.id] = session
        await self._persist(session)

    async def delete(self, session_id: str) -> None:
        """删除会话。"""
        self._sessions.pop(session_id, None)
        if self._persistence:
            self._persistence.delete_session(session_id)

    async def add_message(self, session_id: str, message: Message) -> None:
        """向会话追加消息，持久化并写入 MemoryRecord(scope='session')。"""
        session = await self.get_or_create(session_id)
        session.history.append(message)
        if len(session.history) > self._max_history:
            session.history = session.history[-self._max_history:]
        await self.save(session)

        if self._task_memory:
            from app.models.runtime_contracts import MemoryRecord

            self._task_memory.append_memory_record(
                MemoryRecord(
                    memory_id=f'ses-{uuid4().hex[:12]}',
                    scope='session',
                    namespace={'session_id': session.id},
                    kind='observation',
                    trust_level='verified',
                    source='user' if message.role == 'user' else 'system',
                    summary=message.content[:200],
                    payload={
                        'session_id': session.id,
                        'role': message.role,
                        'content_preview': message.content[:500],
                    },
                    created_at=message.timestamp or datetime.now(timezone.utc),
                )
            )

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

    async def set_agent_name(self, session_id: str, agent_name: str | None) -> None:
        """切换会话使用的 Agent 名称。"""
        session = await self.get_or_create(session_id)
        session.agent_name = agent_name
        await self.save(session)

    async def _persist(self, session: Session) -> None:
        """内部持久化：写入 SQLite。"""
        if self._persistence:
            payload = session.model_dump(mode='json')
            self._persistence.upsert_session(session.id, payload)

    @staticmethod
    def _deserialize(raw: dict[str, Any]) -> Session:
        """从持久化字典反序列化为 Session 对象。"""
        return Session(**raw)

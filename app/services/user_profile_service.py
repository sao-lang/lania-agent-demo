"""用户画像服务模块。

管理用户偏好画像的持久化与推断，支持从 session/run 记忆中自动推断用户偏好。
Phase 6：全新实现，作为统一记忆总线的第 5 层（profile scope）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.runtime_contracts import MemoryRecord

if TYPE_CHECKING:
    from app.agents.memory import TaskMemory
    from app.services.sqlite_store import SQLiteStateStore


class UserProfile(BaseModel):
    """用户画像数据模型。

    存储用户的偏好、习惯和上下文信息，供 LLM 在生成回答时参考。
    """

    user_id: str
    preferences: dict[str, str] = Field(
        default_factory=dict,
        description='用户偏好键值对，如 {"language": "zh-CN", "detail_level": "detailed"}',
    )
    habits: dict[str, str] = Field(
        default_factory=dict,
        description='用户行为习惯，如 {"preferred_tool": "rag_retrieve_evidence"}',
    )
    context: dict[str, str] = Field(
        default_factory=dict,
        description='用户上下文信息，如 {"role": "developer", "domain": "AI"}',
    )
    recent_topics: list[str] = Field(
        default_factory=list,
        description='最近讨论的话题列表',
    )
    total_sessions: int = Field(default=0, description='累计会话数')
    total_tasks: int = Field(default=0, description='累计任务数')
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserProfileService:
    """用户画像服务。

    管理用户画像的 CRUD 和自动推断。
    通过 TaskMemory 写入 MemoryRecord(scope='profile') 实现统一记忆总线接入。
    """

    def __init__(
        self,
        persistence: SQLiteStateStore | None = None,
        task_memory: TaskMemory | None = None,
    ) -> None:
        self._persistence = persistence
        self._task_memory = task_memory
        self._cache: dict[str, UserProfile] = {}

    async def get_or_create(self, user_id: str) -> UserProfile:
        """获取或创建用户画像。

        查找顺序：内存缓存 → SQLite 持久化 → 新建。
        """
        if user_id in self._cache:
            return self._cache[user_id]

        if self._persistence:
            raw = self._persistence.get_user_profile(user_id)
            if raw:
                profile = UserProfile(**raw)
                self._cache[user_id] = profile
                return profile

        profile = UserProfile(user_id=user_id)
        self._cache[user_id] = profile
        await self._persist(profile)
        return profile

    async def get(self, user_id: str) -> UserProfile | None:
        """获取用户画像。"""
        if user_id in self._cache:
            return self._cache[user_id]
        if self._persistence:
            raw = self._persistence.get_user_profile(user_id)
            if raw:
                profile = UserProfile(**raw)
                self._cache[user_id] = profile
                return profile
        return None

    async def save(self, profile: UserProfile) -> None:
        """保存用户画像（双写：内存缓存 + SQLite）。"""
        profile.updated_at = datetime.now(timezone.utc)
        self._cache[profile.user_id] = profile
        await self._persist(profile)

    async def update_preference(self, user_id: str, key: str, value: str) -> UserProfile:
        """更新单个偏好项。"""
        profile = await self.get_or_create(user_id)
        profile.preferences[key] = value
        await self.save(profile)
        return profile

    async def update_habit(self, user_id: str, key: str, value: str) -> UserProfile:
        """更新单个行为习惯。"""
        profile = await self.get_or_create(user_id)
        profile.habits[key] = value
        await self.save(profile)
        return profile

    async def record_session(self, user_id: str) -> None:
        """记录一次会话事件。"""
        profile = await self.get_or_create(user_id)
        profile.total_sessions += 1
        await self.save(profile)

    async def record_task(self, user_id: str) -> None:
        """记录一次任务事件。"""
        profile = await self.get_or_create(user_id)
        profile.total_tasks += 1
        await self.save(profile)

    async def add_topic(self, user_id: str, topic: str) -> None:
        """添加最近话题。"""
        profile = await self.get_or_create(user_id)
        if topic not in profile.recent_topics:
            profile.recent_topics.append(topic)
            if len(profile.recent_topics) > 20:
                profile.recent_topics = profile.recent_topics[-20:]
        await self.save(profile)

    async def infer_from_memory(self, user_id: str) -> UserProfile:
        """从语义记忆和会话记忆中推断用户偏好。

        查询 scope='semantic' 和 scope='session' 的记录，
        提取高频关键词作为偏好推断依据。
        """
        profile = await self.get_or_create(user_id)

        if not self._task_memory:
            return profile

        records: list[MemoryRecord] = self._task_memory.query_memory_records(
            user_id,
            scope='semantic',
            trust_level='verified',
            limit=50,
        )
        records += self._task_memory.query_memory_records(
            user_id,
            scope='session',
            trust_level='verified',
            limit=50,
        )

        if not records:
            return profile

        words = self._extract_keywords([r.summary for r in records if r.summary])
        if words:
            profile.preferences.setdefault('inferred_keywords', ', '.join(words[:10]))
            profile.preferences.setdefault('inferred_at', datetime.now(timezone.utc).isoformat())
            await self.save(profile)

        return profile

    async def commit_to_memory(self, user_id: str) -> None:
        """将画像摘要写入统一记忆总线。

        写入 MemoryRecord(scope='profile')，使画像可被语义检索消费。
        """
        if not self._task_memory:
            return

        profile = await self.get_or_create(user_id)
        self._task_memory.append_memory_record(
            MemoryRecord(
                memory_id=f'prof-{uuid4().hex[:12]}',
                scope='profile',
                kind='artifact',
                trust_level='verified',
                source='user_profile_service',
                summary=f'User profile for {user_id}: {len(profile.preferences)} prefs, {len(profile.habits)} habits, {profile.total_sessions} sessions',
                payload={
                    'user_id': user_id,
                    'preferences': profile.preferences,
                    'habits': profile.habits,
                    'context': profile.context,
                    'recent_topics': profile.recent_topics,
                    'total_sessions': profile.total_sessions,
                    'total_tasks': profile.total_tasks,
                },
                created_at=datetime.now(timezone.utc),
            )
        )

    async def _persist(self, profile: UserProfile) -> None:
        if self._persistence:
            self._persistence.upsert_user_profile(
                profile.user_id,
                profile.model_dump(mode='json'),
            )

    @staticmethod
    def _extract_keywords(texts: list[str], top_n: int = 20) -> list[str]:
        """简单关键词提取：按词频排序。"""
        from collections import Counter
        import re

        word_counter: Counter[str] = Counter()
        for text in texts:
            words = re.findall(r'[\w\u4e00-\u9fff]{2,}', text.lower())
            word_counter.update(words)

        # 过滤停用词
        stopwords = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'can', 'shall',
            'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
            'and', 'or', 'not', 'but', 'if', 'so', 'as', 'it', 'its',
            'this', 'that', 'these', 'those', 'then', 'than', 'just',
            'very', 'also', 'only', 'now', 'new', 'more', 'some',
            'no', 'up', 'out', 'about', 'into', 'over', 'after',
        }
        return [w for w, _ in word_counter.most_common(top_n * 2) if w not in stopwords][:top_n]
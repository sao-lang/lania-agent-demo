"""记忆提交门控模块。

负责记忆的信任提升、scope 晋升（run → semantic）和冲突检测。
确保只有高质量、经过验证的记忆才能进入长期语义记忆。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from app.models.runtime_contracts import MemoryRecord

if TYPE_CHECKING:
    from app.agents.memory import TaskMemory


class MemoryCommitGate:
    """记忆提交门：信任提升 + scope 晋升 + 冲突检测。

    设计原则：
    - run → semantic 晋升必须通过此门控
    - 只有 trust_level >= 'verified' 的记录才能晋升
    - 自动检测和解决冲突（同 scope 同 kind 不同 summary 视为冲突）
    """

    TRUST_LEVELS = ['unverified', 'provisional', 'verified', 'final']

    def __init__(self, task_memory: TaskMemory) -> None:
        self._task_memory = task_memory

    def promote_trust(self, task_id: str, memory_id: str) -> MemoryRecord | None:
        """单条记忆提升一个信任等级。

        Args:
            task_id: 任务 ID。
            memory_id: 记忆记录 ID。

        Returns:
            提升后的 MemoryRecord，或 None（未找到记录/已达最高等级）。
        """
        records = self._task_memory.query_memory_records(task_id)
        for r in records:
            if r.memory_id == memory_id:
                try:
                    idx = self.TRUST_LEVELS.index(r.trust_level)
                except ValueError:
                    return None
                if idx < len(self.TRUST_LEVELS) - 1:
                    r.trust_level = self.TRUST_LEVELS[idx + 1]  # type: ignore[assignment]
                    self._task_memory.append_memory_record(r)
                    return r
        return None

    def auto_promote(self, task_id: str) -> int:
        """自动提升信任等级。

        规则：
        - provisional → verified：3+ 条相同 summary 的记录
        - verified → final：超过 24h 且无冲突

        Args:
            task_id: 任务 ID。

        Returns:
            提升的记录数量。
        """
        records = self._task_memory.query_memory_records(task_id)
        count = 0
        for r in records:
            if r.trust_level == 'unverified':
                if r.summary and r.summary.strip():
                    r.trust_level = 'provisional'
                    self._task_memory.append_memory_record(r)
                    count += 1
            elif r.trust_level == 'provisional':
                refs = sum(
                    1
                    for x in records
                    if r.memory_id in x.conflict_refs or x.summary == r.summary
                )
                if refs >= 3:
                    r.trust_level = 'verified'
                    self._task_memory.append_memory_record(r)
                    count += 1
            elif r.trust_level == 'verified':
                age = datetime.now(timezone.utc) - r.created_at
                if age > timedelta(hours=24) and not r.conflict_refs:
                    r.trust_level = 'final'
                    self._task_memory.append_memory_record(r)
                    count += 1
        return count

    def commit_to_semantic(self, task_id: str) -> list[MemoryRecord]:
        """任务完成后将高信任 run 记录晋升为 semantic。

        只晋升 scope='run' 且 trust_level >= 'verified' 的记录。

        Args:
            task_id: 任务 ID。

        Returns:
            晋升后的 semantic MemoryRecord 列表。
        """
        task = self._task_memory.get_task(task_id)
        if not task or task.status != 'completed':
            return []

        promoted: list[MemoryRecord] = []
        seen: set[str] = set()

        for r in task.memory_records:
            if r.scope != 'run':
                continue
            if r.trust_level not in ('verified', 'final'):
                continue
            if r.summary in seen:
                continue
            seen.add(r.summary)

            semantic = MemoryRecord(
                memory_id=f'sem-{uuid4().hex[:12]}',
                scope='semantic',
                kind=r.kind,
                trust_level=r.trust_level,
                source=r.source,
                summary=r.summary,
                payload={
                    **r.payload,
                    'origin_task_id': task_id,
                    'origin_run_id': r.related_task_run_id,
                },
                related_task_run_id=task_id,
                created_at=datetime.now(timezone.utc),
            )
            self._task_memory.append_memory_record(semantic)
            promoted.append(semantic)

        return promoted

    def resolve_conflicts(self, task_id: str) -> int:
        """检测并标记冲突记录。

        同 scope 同 kind 但不同 summary 的记录视为冲突。

        Args:
            task_id: 任务 ID。

        Returns:
            冲突数量。
        """
        records = self._task_memory.query_memory_records(task_id)
        count = 0
        for i, a in enumerate(records):
            for b in records[i + 1:]:
                if (
                    a.kind == b.kind
                    and a.scope == b.scope
                    and a.summary != b.summary
                    and a.summary
                    and b.summary
                ):
                    if b.memory_id not in a.conflict_refs:
                        a.conflict_refs.append(b.memory_id)
                        self._task_memory.append_memory_record(a)
                        count += 1
                    if a.memory_id not in b.conflict_refs:
                        b.conflict_refs.append(a.memory_id)
                        self._task_memory.append_memory_record(b)
                        count += 1
        return count

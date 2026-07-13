"""查询恢复扩展模块。

负责判断 query run 是否可恢复、定位最新可恢复检查点，并在恢复动作发生后
补记恢复事件。模块只处理恢复元数据，不直接重放工作流执行。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.models.query import QueryRunDetail
from app.models.task import CheckpointRecord
from app.types import QueryRunRecord


class RecoveryManager:
    """query runtime 的最小恢复管理器。"""

    def is_query_run_recoverable(
        self,
        *,
        task_run_status: str,
        checkpoints: list[dict],
        completed_at: datetime | None,
    ) -> bool:
        """判断一次查询运行是否仍具备恢复条件。

        Args:
            task_run_status: 当前运行记录状态。
            checkpoints: 已持久化的检查点列表。
            completed_at: 运行完成时间；已完成的运行不再允许恢复。

        Returns:
            若当前运行仍处于可恢复状态且存在检查点，则返回 ``True``。
        """

        if completed_at is not None and task_run_status == 'completed':
            return False
        return task_run_status in {'running', 'failed'} and bool(checkpoints)

    def latest_recoverable_checkpoint(self, detail: QueryRunDetail) -> CheckpointRecord | None:
        """返回最近一个可用于恢复的检查点。"""

        if not detail.recoverable or not detail.checkpoints:
            return None
        return detail.checkpoints[-1]

    def mark_query_run_recovered(self, record: QueryRunRecord, checkpoint_id: str) -> QueryRunRecord:
        """在运行记录上标记一次恢复动作并追加恢复事件。"""

        updated = dict(record)
        updated['recoverable'] = False
        updated['updated_at'] = datetime.now(timezone.utc)
        updated['run_events'] = [
            *record.get('run_events', []),
            {
                'event_id': f'revt-{uuid4().hex[:12]}',
                'name': 'workflow_resume_started',
                'timestamp': datetime.now(timezone.utc),
                'payload': {'checkpoint_id': checkpoint_id},
            },
        ]
        return updated  # type: ignore[return-value]

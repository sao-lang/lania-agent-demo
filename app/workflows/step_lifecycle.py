"""共享步骤生命周期辅助模块。

负责沉淀 query workflow 与 task workflow 通用的步骤运行态更新逻辑，包括运行事件创建、
步骤开始/完成/失败状态迁移、checkpoint 生成，以及运行态记录的规范化与序列化。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.models.task import CheckpointRecord, ReflectionDecision, StepRuntimeRecord, TaskRun, TaskRunEvent


def create_run_event(name: str, payload: dict[str, Any], *, timestamp: datetime | None = None) -> TaskRunEvent:
    """创建统一运行事件对象。

    Args:
        name: 事件名，例如步骤开始、完成或失败。
        payload: 事件相关的结构化载荷。
        timestamp: 可选事件时间；为空时使用当前 UTC 时间。

    Returns:
        标准化 `TaskRunEvent` 对象。
    """
    return TaskRunEvent(
        event_id=f'revt-{uuid4().hex[:12]}',
        name=name,
        timestamp=timestamp or datetime.now(timezone.utc),
        payload=payload,
    )


def mark_step_started(task_run: TaskRun, step_id: str, *, now: datetime | None = None) -> StepRuntimeRecord:
    """将指定步骤标记为运行中。

    该函数会同时递增尝试次数，并重置上一次运行残留的完成时间、退出原因和降级标记，保证每次
    进入步骤时都拥有一份干净的运行态。
    """
    effective_now = now or datetime.now(timezone.utc)
    task_run.current_step_id = step_id
    task_run.step_attempts[step_id] = int(task_run.step_attempts.get(step_id, 0)) + 1
    runtime = task_run.step_runtimes.get(step_id, StepRuntimeRecord(step_id=step_id))
    runtime.attempt_count = int(task_run.step_attempts.get(step_id, 0))
    runtime.status = 'running'
    runtime.started_at = effective_now
    runtime.completed_at = None
    runtime.exit_reason = None
    runtime.fallback_action_applied = None
    runtime.degraded = False
    runtime.skipped = False
    task_run.step_runtimes[step_id] = runtime
    return runtime


def mark_step_completed(
    task_run: TaskRun,
    step_id: str,
    *,
    completed_step_ids: list[str],
    exit_reason: str = 'completed',
    fallback_action_applied: str | None = None,
    degraded: bool = False,
    skipped: bool = False,
    reflection_decision: ReflectionDecision | None = None,
    now: datetime | None = None,
) -> StepRuntimeRecord:
    """将指定步骤标记为已完成或已跳过。

    当某一步因为降级策略、证据缺口保留等原因被视作“可接受完成”时，也通过这个入口统一写回
    运行态，避免 completed / skipped 两套状态更新逻辑分叉。
    """
    effective_now = now or datetime.now(timezone.utc)
    runtime = task_run.step_runtimes.get(step_id, StepRuntimeRecord(step_id=step_id))
    if runtime.attempt_count <= 0:
        runtime.attempt_count = int(task_run.step_attempts.get(step_id, 0)) or 1
        runtime.started_at = runtime.started_at or effective_now
    runtime.status = 'skipped' if skipped else 'completed'
    runtime.completed_at = effective_now
    runtime.exit_reason = exit_reason
    runtime.fallback_action_applied = fallback_action_applied
    runtime.degraded = degraded
    runtime.skipped = skipped
    task_run.current_step_id = step_id
    task_run.completed_step_ids = list(completed_step_ids)
    task_run.step_attempts[step_id] = runtime.attempt_count
    task_run.step_runtimes[step_id] = runtime
    if reflection_decision is not None:
        task_run.last_reflection_decision = reflection_decision
    return runtime


def mark_step_failed(
    task_run: TaskRun,
    step_id: str,
    *,
    completed_step_ids: list[str],
    error: str,
    now: datetime | None = None,
) -> StepRuntimeRecord:
    """将指定步骤标记为失败。

    Args:
        task_run: 当前任务运行记录。
        step_id: 失败步骤标识。
        completed_step_ids: 失败发生时已经完成的步骤集合。
        error: 失败原因说明。
        now: 可选失败时间；为空时使用当前 UTC 时间。

    Returns:
        更新后的步骤运行态记录。
    """
    effective_now = now or datetime.now(timezone.utc)
    runtime = task_run.step_runtimes.get(step_id, StepRuntimeRecord(step_id=step_id))
    if runtime.attempt_count <= 0:
        runtime.attempt_count = int(task_run.step_attempts.get(step_id, 0)) + 1
    runtime.status = 'failed'
    runtime.started_at = runtime.started_at or effective_now
    runtime.completed_at = effective_now
    runtime.exit_reason = error
    runtime.fallback_action_applied = None
    runtime.degraded = False
    runtime.skipped = False
    task_run.current_step_id = step_id
    task_run.step_attempts[step_id] = runtime.attempt_count
    task_run.step_runtimes[step_id] = runtime
    task_run.completed_step_ids = list(completed_step_ids)
    return runtime


def create_checkpoint(
    *,
    step_id: str,
    next_route: str,
    completed_step_ids: list[str],
    state_snapshot: dict[str, Any],
    created_at: datetime | None = None,
) -> CheckpointRecord:
    """创建统一 checkpoint 对象。

    checkpoint 用于支持任务/查询运行时的重放与恢复，因此会保留步骤位置、下一跳路由、已完成
    步骤集合以及可重建运行态所需的状态快照。
    """
    return CheckpointRecord(
        checkpoint_id=f'ckpt-{uuid4().hex[:12]}',
        step_id=step_id,
        next_route=next_route,
        created_at=created_at or datetime.now(timezone.utc),
        completed_step_ids=list(completed_step_ids),
        state_snapshot=state_snapshot,
    )


def normalize_step_runtimes(
    payload: dict[str, StepRuntimeRecord | dict[str, Any]] | None,
) -> dict[str, StepRuntimeRecord]:
    """把运行态 `step_runtimes` 收口为强类型记录。

    该函数主要用于兼容两类来源：

    - 内存中已构造好的 `StepRuntimeRecord`
    - 从持久化层恢复出来的普通字典
    """
    return {
        key: value if isinstance(value, StepRuntimeRecord) else StepRuntimeRecord.model_validate(value)
        for key, value in (payload or {}).items()
    }


def dump_step_runtimes(
    payload: dict[str, StepRuntimeRecord | dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """把 `step_runtimes` 统一转为可持久化 JSON 结构。"""
    return {
        key: runtime.model_dump(mode='json')
        for key, runtime in normalize_step_runtimes(payload).items()
    }

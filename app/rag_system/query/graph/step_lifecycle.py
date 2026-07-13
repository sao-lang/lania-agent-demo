"""RAG 系统查询工作流步骤生命周期模块。

提供步骤运行态更新逻辑，包括运行事件创建、步骤开始/完成/失败状态迁移、
checkpoint 生成以及运行态记录的规范化。

与 ``app/workflows/step_lifecycle.py`` 功能一致，但仅依赖 rag_system 自有类型。
"""

from __future__ import annotations

from time import time
from typing import Any
from uuid import uuid4

from app.rag_system.query.graph.state import (
    CheckpointRecord,
    StepRuntimeRecord,
    TaskRun,
    TaskRunEvent,
)


def create_run_event(name: str, payload: dict[str, Any], *, timestamp: float | None = None) -> TaskRunEvent:
    """创建统一运行事件对象。"""
    return {
        'event_id': f'revt-{uuid4().hex[:12]}',
        'name': name,
        'timestamp': timestamp or time(),
        'payload': payload,
    }


def create_checkpoint_record(
    step_id: str,
    step_index: int,
    state_snapshot: dict[str, Any],
    *,
    checkpoint_id: str | None = None,
    timestamp: float | None = None,
) -> CheckpointRecord:
    """创建步骤 checkpoint 记录。"""
    return {
        'checkpoint_id': checkpoint_id or f'cp-{uuid4().hex[:12]}',
        'step_id': step_id,
        'step_index': step_index,
        'state_snapshot': state_snapshot,
        'created_at': timestamp or time(),
    }


def mark_step_started(
    task_run: TaskRun,
    step_id: str,
    *,
    now: float | None = None,
) -> StepRuntimeRecord:
    """将指定步骤标记为运行中。

    递增尝试次数，重置完成时间/退出原因/降级标记。
    """
    effective_now = now or time()
    task_run['current_step_id'] = step_id
    attempts = task_run.get('step_attempts', {})
    attempts[step_id] = int(attempts.get(step_id, 0)) + 1
    task_run['step_attempts'] = attempts
    runtimes = task_run.get('step_runtimes', {})
    runtime = runtimes.get(step_id, StepRuntimeRecord(step_id=step_id))
    runtime['attempt_count'] = int(attempts.get(step_id, 0))
    runtime['status'] = 'running'
    runtime['started_at'] = effective_now
    runtime['completed_at'] = None
    runtime['exit_reason'] = None
    runtime['fallback_action_applied'] = None
    runtime['degraded'] = False
    runtime['skipped'] = False
    runtimes[step_id] = runtime
    task_run['step_runtimes'] = runtimes
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
    now: float | None = None,
) -> StepRuntimeRecord:
    """将指定步骤标记为已完成或已跳过。

    当某一步因为降级策略/证据缺口等原因被视作"可接受完成"时，
    也通过这个入口统一写回运行态。
    """
    effective_now = now or time()
    runtimes = task_run.get('step_runtimes', {})
    runtime = runtimes.get(step_id, StepRuntimeRecord(step_id=step_id))
    attempts = task_run.get('step_attempts', {})
    if runtime.get('attempt_count', 0) <= 0:
        runtime['attempt_count'] = int(attempts.get(step_id, 0)) or 1
        runtime['started_at'] = runtime.get('started_at') or effective_now
    runtime['status'] = 'skipped' if skipped else 'completed'
    runtime['completed_at'] = effective_now
    runtime['exit_reason'] = exit_reason
    runtime['fallback_action_applied'] = fallback_action_applied
    runtime['degraded'] = degraded
    runtime['skipped'] = skipped
    runtimes[step_id] = runtime
    task_run['step_runtimes'] = runtimes
    if step_id not in completed_step_ids:
        completed_step_ids.append(step_id)
    return runtime


def mark_step_failed(
    task_run: TaskRun,
    step_id: str,
    *,
    exit_reason: str = 'error',
    now: float | None = None,
) -> StepRuntimeRecord:
    """将指定步骤标记为失败。"""
    effective_now = now or time()
    runtimes = task_run.get('step_runtimes', {})
    runtime = runtimes.get(step_id, StepRuntimeRecord(step_id=step_id))
    runtime['status'] = 'failed'
    runtime['completed_at'] = effective_now
    runtime['exit_reason'] = exit_reason
    runtimes[step_id] = runtime
    task_run['step_runtimes'] = runtimes
    return runtime


def dump_step_runtimes(task_run: TaskRun) -> dict[str, Any]:
    """提取步骤运行时记录为可序列化的扁平字典。"""
    runtimes = task_run.get('step_runtimes', {})
    return {
        step_id: {
            k: v for k, v in rt.items() if v is not None
        }
        for step_id, rt in runtimes.items()
    }


def normalize_step_runtimes(
    runtimes: dict[str, Any],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """规范化步骤运行时记录列表，限制最大条目数。"""
    if not runtimes:
        return []
    normalized = []
    for step_id, rt in runtimes.items():
        if isinstance(rt, dict):
            entry = dict(rt)
            entry['step_id'] = step_id
            normalized.append(entry)
    normalized.sort(key=lambda x: x.get('started_at', 0) or 0, reverse=True)
    return normalized[:limit]

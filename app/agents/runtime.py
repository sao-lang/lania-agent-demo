"""任务级 Agent Runtime 模块。

负责驱动任务从排队态进入运行态，再根据工作流执行结果落入完成或失败状态，并统一记录
任务开始、结束和异常信息。该模块是任务 worker 实际调用的执行入口。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.memory import TaskMemory
from app.agents.tools.base import ToolExecutionError
from app.core.errors import not_found_error
from app.models.task import TaskFailure, TaskResult
from app.rag.observability import TraceRecorder
from app.workflows.tasks.task_orchestrator import TaskWorkflowOrchestrator


class AgentRuntime:
    """驱动任务从 queued 到 completed/failed。"""

    def __init__(self, orchestrator: TaskWorkflowOrchestrator, memory: TaskMemory, trace: TraceRecorder) -> None:
        """初始化任务运行时。

        Args:
            orchestrator: 任务工作流编排器。
            memory: 任务内存与持久化访问封装。
            trace: 链路追踪记录器。
        """
        self.orchestrator = orchestrator
        self.memory = memory
        self.trace = trace

    def run(self, task_id: str) -> TaskResult:
        """执行一次任务。

        Args:
            task_id: 待执行任务 ID。

        Returns:
            任务执行结果。

        Raises:
            Exception: 当任务执行过程中出现未恢复异常时继续向上抛出。
        """

        now = datetime.now(timezone.utc)
        task = self.memory.get_task(task_id)
        if task is None:
            raise not_found_error('task', task_id)
        # 任务真正开始执行前先刷新状态，方便 worker 心跳和前端轮询感知最新阶段。
        task.status = 'running'
        task.started_at = task.started_at or now
        task.completed_at = None
        task.ensure_runtime_contracts()
        if task.task_run is not None:
            task.task_run.start(task.started_at)
        self.memory.upsert_task(task)
        self.trace.record('task_started', {'task_id': task_id, 'task_type': task.request.task_type})
        try:
            result = self.orchestrator.run(task)
            latest = self.memory.get_task(task_id)
            if latest is not None:
                # 工作流内部已经写入完成态结果，这里统一补齐完成时间和租约字段收尾。
                latest.completed_at = datetime.now(timezone.utc)
                latest.heartbeat_at = None
                latest.lease_expires_at = None
                latest.ensure_runtime_contracts()
                if latest.task_run is not None:
                    latest.task_run.sync_progress(
                        current_step_id=latest.current_step,
                        completed_step_ids=latest.completed_steps,
                        completed_at=latest.completed_at,
                    )
                self.memory.upsert_task(latest)
            self.trace.record(
                'task_completed',
                {
                    'task_id': task_id,
                    'status': result.status,
                    'final_artifact_id': result.final_artifact_id,
                    'metrics': result.metrics.model_dump(mode='json'),
                },
            )
            return result
        except Exception as exc:
            latest = self.memory.get_task(task_id) or task
            latest.status = 'failed'
            latest.completed_at = datetime.now(timezone.utc)
            latest.heartbeat_at = None
            latest.lease_expires_at = None
            # 工具错误与普通运行时异常都统一落到 failures，便于任务详情页直接展示失败原因。
            failure_code = exc.code if isinstance(exc, ToolExecutionError) else 'task_execution_failed'
            latest.failures.append(TaskFailure(code=failure_code, message=str(exc)))
            latest.ensure_runtime_contracts()
            if latest.task_run is not None:
                latest.task_run.sync_progress(
                    current_step_id=latest.current_step,
                    completed_step_ids=latest.completed_steps,
                    completed_at=latest.completed_at,
                    failed=True,
                )
            self.memory.upsert_task(latest)
            self.trace.record(
                'task_completed',
                {
                    'task_id': task_id,
                    'status': 'failed',
                    'error': str(exc),
                    'error_code': failure_code,
                    'error_type': exc.error_type if isinstance(exc, ToolExecutionError) else None,
                },
            )
            raise

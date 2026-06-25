"""ExecutionHarness 观测与记账钩子模块。

负责从 workflow state 提取 owner 信息，并统一记录工具执行摘要、运行时记忆
与 trace 事件，避免执行主流程散落重复的观测逻辑。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.agents.memory import TaskMemory
from app.harness.models import ExecutionRuntimeSummary, ToolExecutionResult
from app.rag.observability import TraceRecorder


class ExecutionHooks:
    """记录运行时摘要与工具执行 trace。"""

    def __init__(self, memory: TaskMemory, trace: TraceRecorder) -> None:
        """初始化记忆与 trace 记录依赖。"""

        self.memory = memory
        self.trace = trace

    def workflow_owner_id(self, workflow_state: dict[str, Any]) -> str | None:
        """提取当前 workflow 对应的 task_id 或 run_id。"""

        task = workflow_state.get('task')
        if task is not None:
            return getattr(task, 'task_id', None)
        task_run = workflow_state.get('task_run')
        if task_run is not None:
            return getattr(task_run, 'run_id', None)
        return None

    def workflow_owner_step(self, workflow_state: dict[str, Any]) -> str | None:
        """提取当前 workflow 所处步骤。"""

        task = workflow_state.get('task')
        if task is not None:
            return getattr(task, 'current_step', None)
        current_step_id = workflow_state.get('current_step_id')
        if isinstance(current_step_id, str):
            return current_step_id
        task_run = workflow_state.get('task_run')
        if task_run is not None:
            return getattr(task_run, 'current_step_id', None)
        return None

    def workflow_run_budget(self, workflow_state: dict[str, Any]):
        """提取当前运行预算对象，供模型路由或工具执行复用。"""

        task = workflow_state.get('task')
        if task is not None and getattr(task, 'request', None) is not None:
            request = getattr(task, 'request', None)
            if request is not None and hasattr(request, 'to_run_budget'):
                return request.to_run_budget()
        task_run = workflow_state.get('task_run')
        if task_run is not None:
            return getattr(task_run, 'budget', None)
        return None

    def has_task_request(self, workflow_state: dict[str, Any]) -> bool:
        """判断当前状态是否携带完整任务请求。"""

        task = workflow_state.get('task')
        request = getattr(task, 'request', None) if task is not None else None
        return request is not None and hasattr(request, 'instructions')

    def derive_warnings(self, result: BaseModel | None) -> list[str]:
        """从工具结果中提取统一 warning 标签。"""

        if result is None:
            return []
        payload = result.model_dump(mode='json')
        warnings: list[str] = []
        if isinstance(payload, dict):
            if payload.get('missing_aspects'):
                warnings.append('missing_aspects_present')
            if payload.get('open_questions'):
                warnings.append('open_questions_present')
        return warnings

    def record_runtime_summary(self, workflow_state: dict[str, Any], summary: ExecutionRuntimeSummary) -> None:
        """把运行时摘要写入任务记忆。"""

        if workflow_state.get('task') is None:
            return
        self.memory.append_task_memory(
            workflow_state['task'].task_id,
            workflow_state['task'].current_step or summary.step_id,
            'state',
            f'Execution runtime 已处理工具 {summary.tool_name}，状态为 {summary.status}。',
            payload={
                'runtime_category': 'execution',
                'tool_name': summary.tool_name,
                'step_id': summary.step_id,
                'status': summary.status,
                'selected_action': summary.selected_action,
                'failure_category': summary.failure_category,
                'retry_count': summary.retry_count,
                'timeout_budget_ms': summary.timeout_budget_ms,
                'sandbox_mode': summary.sandbox_mode,
                'circuit_breaker_open': summary.circuit_breaker_open,
                'used_fallback': summary.used_fallback,
                'attempts': [item.model_dump(mode='json') for item in summary.attempts],
                'trace_id': summary.trace_id,
            },
        )

    def record_execution(
        self,
        workflow_state: dict[str, Any],
        step_id: str,
        execution: ToolExecutionResult,
    ) -> None:
        """记录单次工具执行事件到 trace。"""

        self.trace.record(
            'harness_tool_execution',
            {
                'task_id': self.workflow_owner_id(workflow_state),
                'step_id': step_id,
                'tool_name': execution.tool_name,
                'status': execution.status,
                'failure_category': execution.failure_category,
                'selected_action': execution.selected_action,
                'latency_ms': execution.latency_ms,
                'retries': execution.retries,
                'timeout_budget_ms': execution.timeout_budget_ms,
                'sandbox_mode': execution.sandbox_mode,
                'warnings': execution.warnings,
                'errors': execution.errors,
                'trace_id': execution.trace_id,
            },
        )

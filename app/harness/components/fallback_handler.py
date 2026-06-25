"""ExecutionHarness 降级处理模块。

负责在工具调用失败但允许走 fallback/degrade 分支时，统一生成降级结果并记录
任务记忆与 trace，保证后续排障能看到完整降级路径。
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel

from app.agents.memory import TaskMemory
from app.agents.tools.base import ToolExecutionError
from app.harness.models import ContextBundle
from app.rag.observability import TraceRecorder

FallbackFactory = Callable[[ToolExecutionError], BaseModel]


class FallbackHandler:
    """应用降级行为并记录降级路径。"""

    def __init__(self, memory: TaskMemory, trace: TraceRecorder) -> None:
        """初始化降级处理所需的记忆与 trace 依赖。"""

        self.memory = memory
        self.trace = trace

    def apply(
        self,
        exc: ToolExecutionError,
        *,
        tool_name: str,
        tool_call_id: str,
        workflow_state: dict[str, Any],
        context_bundle: ContextBundle,
        effective_action: str,
        fallback_factory: FallbackFactory,
        workflow_owner_id: str | None,
    ) -> BaseModel:
        """执行 fallback 工厂并把降级信息写入观测数据。"""

        result = fallback_factory(exc)
        self.trace.record(
            'task_tool_fallback_applied',
            {
                'task_id': workflow_owner_id,
                'tool_name': tool_name,
                'tool_call_id': tool_call_id,
                'error_code': exc.code,
                'error_type': exc.error_type,
                'default_action': effective_action,
                'context_step_id': context_bundle.step_id,
                'context_tool_options': context_bundle.tool_options,
            },
        )
        if workflow_state.get('task') is not None:
            self.memory.append_task_memory(
                workflow_state['task'].task_id,
                workflow_state['task'].current_step or 'unknown',
                'state',
                f'工具 {tool_name} 执行失败，已按 {effective_action} 进入降级分支。',
                payload={
                    'tool_name': tool_name,
                    'error_code': exc.code,
                    'error_type': exc.error_type,
                    'default_action': effective_action,
                    'context_step_id': context_bundle.step_id,
                },
            )
        return result

"""TraceHook 实现——通过 EventBus 统一记录运行时事件到 trace。

将原来散落在 workflow/orchestrator/harness 中的 trace.record() 调用，
收口为注册到 HookRegistry 的标准 hook，保证同样的运行事件不会再因
调用方不同而使用不同的 payload 格式。
"""

from __future__ import annotations

from typing import Any

from app.harness.core.hooks import EventPayload, RuntimeHook
from app.rag.observability import TraceRecorder


class TraceHook(RuntimeHook):
    """监听所有运行时事件并记录到 TraceRecorder。

    注册为通配符 hook（event='all'）时，每个运行时事件都会触发一次
    trace.record()，trace 事件名即为 HookEvent 的 value。

    如果只需要特定事件，可以按事件类型注册。
    """

    def __init__(self, trace: TraceRecorder, name: str = 'trace_hook') -> None:
        self._trace = trace
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def handle(self, event: EventPayload) -> None:
        """将事件转发到 trace 记录器。"""

        payload: dict[str, Any] = {
            'hook_event': event.event.value,
            'payload': event.payload,
            'metadata': event.metadata,
        }
        # 从 workflow_state 中摘取 owner 信息，保持与原有 trace 事件一致
        ws = event.workflow_state or {}
        task = ws.get('task')
        if task is not None:
            payload['task_id'] = getattr(task, 'task_id', None)
        task_run = ws.get('task_run')
        if task_run is not None:
            payload['run_id'] = getattr(task_run, 'run_id', None)

        self._trace.record(event.event.value, payload)


class MemoryHook(RuntimeHook):
    """监听特定运行时事件并写入 TaskMemory。

    通常注册为 on_stage_completed / on_tool_execution 等事件，
    自动将运行时状态摘要写入任务记忆。
    """

    def __init__(self, memory: Any, name: str = 'memory_hook') -> None:
        self._memory = memory
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def handle(self, event: EventPayload) -> None:
        """将事件摘要写入任务记忆。"""
        ws = event.workflow_state or {}
        task = ws.get('task')
        if task is None:
            return
        task_id = getattr(task, 'task_id', None)
        if task_id is None:
            return

        step = event.payload.get('step_name') or (
            getattr(task, 'current_step', None)
        )

        summary = f'运行时事件: {event.event.value}'
        self._memory.append_task_memory(
            task_id,
            step or 'runtime',
            'state',
            summary,
            payload={
                'hook_event': event.event.value,
                'payload': event.payload,
                'metadata': event.metadata,
            },
        )
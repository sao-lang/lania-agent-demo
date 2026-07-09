"""Hook Registry 与运行时事件总线模块。

定义统一的 hook 协议和事件总线，让治理（trace/audit/checkpoint）与业务执行
自然解耦。所有运行时扩展点都通过本模块注册，不再需要在 stage/executor 中
手动散写 trace.record。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class HookEvent(str, Enum):
    """运行时统一事件枚举。"""

    # Stage 生命周期
    BEFORE_STAGE = 'before_stage'
    AFTER_STAGE = 'after_stage'
    STAGE_FAILED = 'stage_failed'

    # Tool 生命周期
    BEFORE_TOOL = 'before_tool'
    AFTER_TOOL = 'after_tool'
    TOOL_FAILED = 'tool_failed'

    # Step / ReAct 生命周期
    BEFORE_REACT_TURN = 'before_react_turn'
    AFTER_REACT_TURN = 'after_react_turn'
    REACT_EXCEEDED_MAX_TURNS = 'react_exceeded_max_turns'

    # Checkpoint 生命周期
    BEFORE_CHECKPOINT = 'before_checkpoint'
    AFTER_CHECKPOINT = 'after_checkpoint'

    # Request / Run 生命周期
    RUN_STARTED = 'run_started'
    RUN_COMPLETED = 'run_completed'
    RUN_FAILED = 'run_failed'

    # Recovery 生命周期
    RECOVERY_INITIATED = 'recovery_initiated'
    RECOVERY_COMPLETED = 'recovery_completed'
    RECOVERY_FAILED = 'recovery_failed'

    # Context 生命周期
    CONTEXT_BUILT = 'context_built'
    CONTEXT_TRIM = 'context_trim'


@dataclass
class EventPayload:
    """事件载荷，挂载触发时的工作流上下文和事件特定数据。"""

    event: HookEvent
    workflow_state: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeHook(Protocol):
    """运行时 hook 协议。"""

    name: str

    def handle(self, event: EventPayload) -> None:
        """处理一次 hook 事件。"""


class HookRegistry:
    """按事件类型注册和管理 hook 处理器。

    支持通配符 'all' 注册——监听所有事件的 handler，
    简化全局 trace 记录。
    """

    def __init__(self) -> None:
        self._handlers: dict[HookEvent | str, list[RuntimeHook]] = {}
        self._wildcard_handlers: list[RuntimeHook] = []

    def register(
        self, hook: RuntimeHook, event: HookEvent | str | None = None
    ) -> None:
        """注册单个 hook。

        Args:
            hook: 实现 RuntimeHook 协议的实例。
            event: 要监听的特定事件；为 None 或 'all' 时监听全部事件。
        """
        if event is None or event == 'all':
            self._wildcard_handlers.append(hook)
        else:
            self._handlers.setdefault(event, []).append(hook)

    def register_many(
        self, hooks: list[RuntimeHook], event: HookEvent | str | None = None
    ) -> None:
        """批量注册多个 hook。"""
        for hook in hooks:
            self.register(hook, event=event)

    def emit(self, event: EventPayload) -> None:
        """发射事件到所有匹配的 handler。"""
        # 先跑通配符 handler
        for handler in self._wildcard_handlers:
            handler.handle(event)
        # 再跑特定事件 handler
        for handler in self._handlers.get(event.event, []):
            handler.handle(event)

    def list_registered(self) -> list[tuple[str, str]]:
        """列出所有注册记录，返回 (hook_name, event) 元组列表。"""
        result: list[tuple[str, str]] = []
        for handler in self._wildcard_handlers:
            result.append((handler.name, 'all'))
        for event, handlers in self._handlers.items():
            for handler in handlers:
                evt = event.value if isinstance(event, HookEvent) else event
                result.append((handler.name, evt))
        return result


class EventBus:
    """运行时事件总线，封装 HookRegistry 并提供快捷发射方法。

    用法::

        bus = EventBus(registry)
        bus.before_stage(workflow_state, {'stage_name': 'retrieve'})
    """

    def __init__(self, registry: HookRegistry | None = None) -> None:
        self.registry = registry or HookRegistry()

    def register(
        self, hook: RuntimeHook, event: HookEvent | str | None = None
    ) -> None:
        self.registry.register(hook, event=event)

    def emit(
        self,
        event: HookEvent,
        workflow_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        payload = EventPayload(
            event=event, workflow_state=workflow_state, payload=kwargs
        )
        self.registry.emit(payload)

    # ── 快捷发射方法 ──────────────────────────────────────────────

    def before_stage(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.BEFORE_STAGE, workflow_state, **kwargs)

    def after_stage(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.AFTER_STAGE, workflow_state, **kwargs)

    def stage_failed(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.STAGE_FAILED, workflow_state, **kwargs)

    def before_tool(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.BEFORE_TOOL, workflow_state, **kwargs)

    def after_tool(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.AFTER_TOOL, workflow_state, **kwargs)

    def tool_failed(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.TOOL_FAILED, workflow_state, **kwargs)

    def before_checkpoint(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.BEFORE_CHECKPOINT, workflow_state, **kwargs)

    def after_checkpoint(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.AFTER_CHECKPOINT, workflow_state, **kwargs)

    def run_started(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.RUN_STARTED, workflow_state, **kwargs)

    def run_completed(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.RUN_COMPLETED, workflow_state, **kwargs)

    def run_failed(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.RUN_FAILED, workflow_state, **kwargs)

    def context_built(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.CONTEXT_BUILT, workflow_state, **kwargs)

    def context_trim(
        self, workflow_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self.emit(HookEvent.CONTEXT_TRIM, workflow_state, **kwargs)
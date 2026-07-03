"""Harness Kernel 实现模块。

提供一个按阶段顺序依次执行的轻量内核，集成 EventBus 事件发射，
使治理（trace/audit/checkpoint）与业务执行自然解耦。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.harness.core.hooks import EventBus, HookRegistry


@dataclass
class HarnessState:
    """可在阶段之间传递的基础状态。"""

    payload: dict = field(default_factory=dict)
    current_stage: str | None = None
    completed_stage_ids: list[str] = field(default_factory=list)
    stage_errors: dict[str, str] = field(default_factory=dict)


@dataclass
class HarnessResult:
    """内核执行结果。"""

    state: HarnessState
    completed: bool = True
    error: str | None = None
    failed_stage: str | None = None


class HarnessKernel:
    """按顺序执行 recipe stages 的轻量内核，集成事件总线。

    用法::

        kernel = HarnessKernel(event_bus=bus)
        result = kernel.run(recipe, state, ctx)
    """

    def __init__(
        self, event_bus: EventBus | None = None, bus: EventBus | None = None
    ) -> None:
        """初始化内核及事件总线。

        Args:
            event_bus: 运行时事件总线；为 None 时自动创建空总线。
            bus: event_bus 的别名，方便已有调用兼容。
        """
        self.event_bus = event_bus or bus or EventBus()

    @property
    def hooks(self) -> HookRegistry:
        """获取事件总线底层的 hook registry。"""
        return self.event_bus.registry

    def run(
        self,
        recipe: Any,
        state: HarnessState,
        ctx: Any,
        *,
        workflow_state: dict[str, Any] | None = None,
    ) -> HarnessResult:
        """顺序执行 recipe 中的全部 stage，并在关键节点发射事件。

        Args:
            recipe: 实现 HarnessRecipe 协议的 recipe 实例。
            state: 初始状态。
            ctx: 运行时上下文。
            workflow_state: 可选完整工作流状态，随事件发射供 hook 消费。

        Returns:
            执行结果，包含最终状态和可能的错误信息。
        """
        current_state = state
        ws = workflow_state

        # 发射 run_started
        self.event_bus.run_started(
            ws, recipe_name=getattr(recipe, 'name', str(recipe))
        )

        try:
            for stage in recipe.stages():
                stage_name = getattr(stage, 'name', str(stage))
                current_state.current_stage = stage_name

                # 发射 before_stage
                self.event_bus.before_stage(ws, stage_name=stage_name)

                try:
                    current_state.payload = stage.run(
                        dict(current_state.payload), ctx
                    )
                except Exception as exc:
                    # 发射 stage_failed
                    error_msg = str(exc)
                    current_state.stage_errors[stage_name] = error_msg
                    self.event_bus.stage_failed(
                        ws,
                        stage_name=stage_name,
                        error=error_msg,
                    )
                    return HarnessResult(
                        state=current_state,
                        completed=False,
                        error=error_msg,
                        failed_stage=stage_name,
                    )

                current_state.completed_stage_ids.append(stage_name)

                # 发射 after_stage
                self.event_bus.after_stage(ws, stage_name=stage_name)

            # 发射 run_completed
            self.event_bus.run_completed(
                ws,
                completed_stages=list(current_state.completed_stage_ids),
            )
            return HarnessResult(state=current_state, completed=True)

        except Exception as exc:
            error_msg = str(exc)
            self.event_bus.run_failed(
                ws,
                error=error_msg,
                completed_stages=list(current_state.completed_stage_ids),
            )
            return HarnessResult(
                state=current_state,
                completed=False,
                error=error_msg,
            )
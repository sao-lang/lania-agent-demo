"""Harness Kernel 最小实现模块。

提供一个按阶段顺序依次执行的轻量内核，用最少状态字段串起 recipe 与 stage，
方便上层 runtime 在不引入复杂调度器的前提下复用统一执行骨架。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HarnessState:
    """可在阶段之间传递的基础状态。"""

    payload: dict = field(default_factory=dict)
    current_stage: str | None = None
    completed_stage_ids: list[str] = field(default_factory=list)


@dataclass
class HarnessResult:
    """内核执行结果。"""

    state: HarnessState
    completed: bool = True
    error: str | None = None


class HarnessKernel:
    """按顺序执行 recipe stages 的薄内核。"""

    def run(self, recipe: Any, state: HarnessState, ctx: Any) -> HarnessResult:
        """顺序执行 recipe 中的全部阶段并返回结果摘要。"""

        current_state = state
        try:
            for stage in recipe.stages():
                current_state.current_stage = stage.name
                current_state.payload = stage.run(dict(current_state.payload), ctx)
                current_state.completed_stage_ids.append(stage.name)
            return HarnessResult(state=current_state, completed=True)
        except Exception as exc:
            return HarnessResult(state=current_state, completed=False, error=str(exc))

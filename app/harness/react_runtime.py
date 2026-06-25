"""有界本地 ReAct 运行时模块。

负责在单个 step 内执行有限轮动作选择，不接管任务级 workflow，只输出当前
步骤的局部决策，强调有界、可解释和低耦合。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.harness.models import ContextBundle
from app.models.task import StepSpec


class ReActTurn(BaseModel):
    """单轮局部 ReAct 观察。"""

    turn_index: int = Field(default=0, ge=0)
    action: str
    reason: str
    observation: dict[str, Any] = Field(default_factory=dict)


class ReActState(BaseModel):
    """单步局部 ReAct 状态。"""

    step_id: str
    objective: str
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=1, ge=1, le=8)
    turns: list[ReActTurn] = Field(default_factory=list)
    selected_action: str | None = None
    stop_reason: str | None = None
    success: bool = False


class BoundedLocalReActRuntime:
    """基于 StepSpec 的局部 ReAct 决策器。"""

    def initialize(self, step: StepSpec, context: ContextBundle) -> ReActState:
        """初始化单步执行状态。"""
        return ReActState(
            step_id=step.step_id,
            objective=step.objective or context.objective,
            allowed_tools=list(step.allowed_tools or context.tool_options),
            max_turns=step.max_turns,
        )

    def next_action(self, state: ReActState, context: ContextBundle) -> str | None:
        """选择下一动作。

        当前实现先提供受控启发式策略，优先保证有界与可解释，后续可替换为模型驱动版本。
        """
        if state.stop_reason is not None or len(state.turns) >= state.max_turns:
            state.stop_reason = state.stop_reason or 'max_turns_reached'
            return None
        allowed_tools = state.allowed_tools or list(context.tool_options)
        if not allowed_tools:
            state.stop_reason = 'no_allowed_tools'
            return None
        if len(allowed_tools) == 1:
            return allowed_tools[0]
        if context.memory_slice.get('missing_aspects') and 'retrieve_graph_evidence' in allowed_tools:
            return 'retrieve_graph_evidence'
        if context.evidence_slice and 'review_report' in allowed_tools:
            return 'review_report'
        return allowed_tools[0]

    def observe(
        self,
        state: ReActState,
        *,
        action: str,
        observation: dict[str, Any] | None = None,
        success: bool = False,
        stop_reason: str | None = None,
    ) -> ReActState:
        """记录一轮执行观察。"""
        turn = ReActTurn(
            turn_index=len(state.turns),
            action=action,
            reason=self._reason_for_action(state, action),
            observation=observation or {},
        )
        state.turns.append(turn)
        state.selected_action = action
        state.success = success
        if success:
            state.stop_reason = stop_reason or 'success_criteria_satisfied'
        elif len(state.turns) >= state.max_turns:
            state.stop_reason = stop_reason or 'max_turns_reached'
        return state

    def _reason_for_action(self, state: ReActState, action: str) -> str:
        """为当前动作生成可解释的原因说明。"""

        if action == 'retrieve_graph_evidence':
            return 'memory indicates evidence gaps, route to graph retrieval'
        if action == 'review_report':
            return 'artifact and evidence are present, prefer bounded review'
        if action in state.allowed_tools:
            return 'selected from allowed tools under bounded step runtime'
        return 'fallback action outside explicit tool list'

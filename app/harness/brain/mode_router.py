"""模式路由模块。

根据 IntentDecision 决定最终执行模式。
模式只是整体交互基调，不是安全门控。
"""

from __future__ import annotations

from app.harness.brain.models import (
    IntentDecision,
    RiskLevel,
    RouteContext,
    RouteResult,
    SuggestedMode,
)


class ModeRouter:
    """根据 IntentDecision 决定最终执行模式。

    模式只是整体交互基调，不是安全门控。
    安全门控由 StepExecutor 中的确认矩阵处理。
    """

    async def route(
        self,
        decision: IntentDecision,
        context: RouteContext | None = None,
    ) -> RouteResult:
        """根据意图决策和上下文路由到最终模式。

        Args:
            decision: 意图识别结果。
            context: 路由上下文。

        Returns:
            路由结果（含模式 + 升级原因）。
        """
        ctx = context or RouteContext()
        mode = decision.suggested_mode
        reason = ""

        # ── 升级规则 ──
        # 1. 风险 critical → 强制 plan_confirm
        if decision.risk_level == RiskLevel.CRITICAL and mode != SuggestedMode.PLAN_CONFIRM:
            mode = SuggestedMode.PLAN_CONFIRM
            reason = "风险等级 critical，需要计划+二次确认"

        # 2. 知识来源 ≥ 3 → 需要计划
        elif len(decision.suggested_sources) >= 3 and mode not in (
            SuggestedMode.PLAN, SuggestedMode.PLAN_CONFIRM,
        ):
            mode = SuggestedMode.PLAN
            reason = f"需要 {len(decision.suggested_sources)} 个知识来源 {decision.suggested_sources}，需要规划"

        # 3. 需要规划标志 → plan
        elif decision.needs_planning and mode == SuggestedMode.CHAT:
            mode = SuggestedMode.PLAN
            reason = "意图识别建议需要规划"

        # 4. 用户偏好确认 → plan
        if ctx.user_prefers_confirmation and mode == SuggestedMode.CHAT:
            mode = SuggestedMode.PLAN
            reason = "用户偏好确认模式"

        # 5. 高风险 + 当前 chat → 升级到 autopilot
        if decision.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and mode == SuggestedMode.CHAT:
            mode = SuggestedMode.AUTOPILOT
            reason = f"风险等级 {decision.risk_level.value}，升级到 autopilot 模式"

        return RouteResult(mode=mode, upgrade_reason=reason)

    @staticmethod
    def consent_matrix(mode: SuggestedMode, step_risk: str) -> bool:
        """查询步骤确认矩阵：给定 mode 和步骤风险，是否需要用户确认。

        确认矩阵（来自设计文档附录 C）：

        | mode          | low  | medium | high | critical |
        |---------------|------|--------|------|----------|
        | chat          | auto | auto   | 确认  | 确认     |
        | autopilot     | auto | 披露    | 确认  | 确认     |
        | plan          | auto | 披露    | 确认  | 确认     |
        | plan_confirm  | auto | 确认    | 确认  | 确认     |
        """
        risk_order = ["low", "medium", "high", "critical"]
        risk_idx = risk_order.index(step_risk) if step_risk in risk_order else 3

        MATRIX = {
            SuggestedMode.CHAT:         (False, False, True,  True),
            SuggestedMode.AUTOPILOT:    (False, False, True,  True),
            SuggestedMode.PLAN:         (False, False, True,  True),
            SuggestedMode.PLAN_CONFIRM: (False, True,  True,  True),
        }

        return MATRIX.get(mode, (False, False, True, True))[risk_idx]

    @staticmethod
    def needs_disclosure(mode: SuggestedMode, step_risk: str) -> bool:
        """给定 mode 和步骤风险，是否需要向用户披露。"""
        DISCLOSE_MODES = {SuggestedMode.AUTOPILOT, SuggestedMode.PLAN, SuggestedMode.PLAN_CONFIRM}
        return mode in DISCLOSE_MODES and step_risk in ("medium", "high", "critical")

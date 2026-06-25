"""Guardrail Harness 实现。

负责把输入、计划、工具调用、产物和最终输出的约束收口成统一校验入口。
"""

from __future__ import annotations

from typing import Any

from app.agents.tools.registry import ToolRegistry
from app.harness.components.guardrail_checks import GuardrailEvaluator
from app.harness.components.guardrail_raiser import GuardrailErrorRaiser
from app.harness.models import GuardrailDecision
from app.models.artifact import ReportArtifactContent, ReviewResult
from app.models.task import TaskPlan, TaskRequest
from app.services.state import InMemoryState


class GuardrailEngine:
    """最小可用的任务 guardrail 引擎。"""

    def __init__(self, registry: ToolRegistry) -> None:
        """装配 guardrail 评估器与异常抛出适配器。"""

        self.registry = registry
        self.evaluator = GuardrailEvaluator(registry)
        self.error_raiser = GuardrailErrorRaiser()

    def validate_input(self, request: TaskRequest, state: InMemoryState) -> GuardrailDecision:
        """校验任务输入是否合法且可执行。"""

        return self.evaluator.validate_input(request, state)

    def validate_plan(self, plan: TaskPlan) -> GuardrailDecision:
        """校验计划结构是否满足执行约束。"""

        return self.evaluator.validate_plan(plan)

    def validate_tool_call(
        self,
        tool_name: str,
        payload: dict[str, Any],
        allowed_tools: list[str] | None = None,
    ) -> GuardrailDecision:
        """校验单次工具调用是否越界。"""

        return self.evaluator.validate_tool_call(tool_name, payload, allowed_tools)

    def validate_artifact(self, artifact: ReportArtifactContent, *, stage: str = 'artifact') -> GuardrailDecision:
        """校验中间或最终产物的内容约束。"""

        return self.evaluator.validate_artifact(artifact, stage=stage)

    def validate_output(
        self,
        result: ReportArtifactContent,
        *,
        review: ReviewResult | None,
        output_format: str,
    ) -> GuardrailDecision:
        """校验最终输出是否满足发布要求。"""

        return self.evaluator.validate_output(result, review=review, output_format=output_format)

    def raise_input_error(self, decision: GuardrailDecision) -> None:
        """把输入阶段决策转换为统一异常。"""

        self.error_raiser.raise_input_error(decision)

    def raise_plan_error(self, decision: GuardrailDecision) -> None:
        """把计划阶段决策转换为统一异常。"""

        self.error_raiser.raise_plan_error(decision)

    def raise_tool_error(self, decision: GuardrailDecision) -> None:
        """把工具阶段决策转换为统一异常。"""

        self.error_raiser.raise_tool_error(decision)

    def raise_runtime_error(self, decision: GuardrailDecision) -> None:
        """把运行时阶段决策转换为统一异常。"""

        self.error_raiser.raise_runtime_error(decision)

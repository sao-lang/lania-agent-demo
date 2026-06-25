"""Guardrail 异常适配模块。

把 ``GuardrailDecision`` 转换为任务系统能识别的统一异常类型，方便调用方按
阶段处理输入错误、工具错误和运行时错误。
"""

from __future__ import annotations

from app.agents.tools.base import ToolExecutionError
from app.core.errors import bad_request_error
from app.harness.models import GuardrailDecision


class GuardrailErrorRaiser:
    """把 guardrail 决策转换为运行时异常。"""

    def raise_input_error(self, decision: GuardrailDecision) -> None:
        """在输入阶段决策不通过时抛出 400 错误。"""

        if decision.allowed:
            return
        raise bad_request_error(decision.code, decision.reason, decision.details)

    def raise_plan_error(self, decision: GuardrailDecision) -> None:
        """在计划阶段决策不通过时抛出运行时错误。"""

        if decision.allowed:
            return
        raise RuntimeError(f'{decision.code}: {decision.reason}')

    def raise_tool_error(self, decision: GuardrailDecision) -> None:
        """在工具阶段决策不通过时抛出统一工具错误。"""

        if decision.allowed:
            return
        raise ToolExecutionError(
            code=decision.code,
            message=decision.reason,
            error_type='permission_error',
            default_action='abort',
            details=decision.details,
        )

    def raise_runtime_error(self, decision: GuardrailDecision) -> None:
        """在运行时阶段决策不通过时抛出通用运行时异常。"""

        if decision.allowed:
            return
        raise RuntimeError(f'{decision.code}: {decision.reason}')

"""计算器工具模块�?

封装安全数学计算能力�?LLM 可调用的工具函数�?
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent_platform.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.agent_platform.capabilities.calculator import CalculatorCapability


class CalculateInput(BaseModel):
    """数学表达式求值的输入参数�?""
    expression: str = Field(description='数学表达式，�?"3.14 * 2^2"�?sqrt(144) + pi"')


class CalculateOutput(BaseModel):
    """计算结果输出�?""
    expression: str
    result: float
    explanation: str


class CalculateTool:
    """计算数学表达式。支持四则运算、幂运算、数学函数（sqrt, sin, cos 等）和常量（pi, e）�?""

    name = 'calculate'
    version = 'v1'
    timeout_ms = 5_000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = CalculateInput
    output_model = CalculateOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: CalculateInput, context) -> CalculateOutput:
        """执行数学计算�?""
        cap = CalculatorCapability()
        try:
            result = cap.calculate(payload.expression)
        except ValueError as exc:
            raise ToolExecutionError(
                code='calculation_error',
                message=str(exc),
                error_type='validation_error',
                default_action='abort',
            ) from exc
        return CalculateOutput(
            expression=result.expression,
            result=result.result,
            explanation=result.explanation,
        )

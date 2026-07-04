"""图表生成工具模块。

封装图表生成能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.chart import ChartCapability


class GenerateChartInput(BaseModel):
    """生成图表的输入参数。"""
    chart_type: str = Field(description='图表类型：bar（柱状图）/ line（折线图）/ pie（饼图）/ scatter（散点图）')
    labels: list[str] = Field(description='数据标签列表')
    values: list[float] = Field(description='数据值列表')
    title: str = Field(default='', description='图表标题')
    xlabel: str = Field(default='', description='X 轴标签')
    ylabel: str = Field(default='', description='Y 轴标签')


class GenerateChartOutput(BaseModel):
    """图表生成结果输出。"""
    title: str
    chart_type: str
    image_base64: str
    description: str


class GenerateChartTool:
    """生成统计图表（柱状图、折线图、饼图、散点图），返回 base64 编码的 PNG 图片。"""

    name = 'generate_chart'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GenerateChartInput
    output_model = GenerateChartOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GenerateChartInput, context) -> GenerateChartOutput:
        """生成图表。"""
        cap = ChartCapability()
        try:
            chart_type = payload.chart_type.lower()
            if chart_type == 'bar':
                result = cap.generate_bar_chart(
                    labels=payload.labels, values=payload.values,
                    title=payload.title, xlabel=payload.xlabel, ylabel=payload.ylabel,
                )
            elif chart_type == 'line':
                result = cap.generate_line_chart(
                    x_data=payload.labels, y_data=payload.values,
                    title=payload.title, xlabel=payload.xlabel, ylabel=payload.ylabel,
                )
            elif chart_type == 'pie':
                result = cap.generate_pie_chart(
                    labels=payload.labels, values=payload.values,
                    title=payload.title,
                )
            elif chart_type == 'scatter':
                result = cap.generate_scatter_chart(
                    x_data=payload.values[:len(payload.values)//2] if len(payload.values) > 1 else payload.values,
                    y_data=payload.values[len(payload.values)//2:] if len(payload.values) > 1 else payload.values,
                    title=payload.title, xlabel=payload.xlabel, ylabel=payload.ylabel,
                )
            else:
                raise ValueError(f'unsupported chart type: {chart_type}')
        except RuntimeError as exc:
            raise ToolExecutionError(
                code='chart_dependency_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        except ValueError as exc:
            raise ToolExecutionError(
                code='chart_input_error',
                message=str(exc),
                error_type='validation_error',
                default_action='abort',
            ) from exc
        return GenerateChartOutput(
            title=result.title,
            chart_type=result.chart_type,
            image_base64=result.image_base64,
            description=result.description,
        )

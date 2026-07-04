"""时间日期工具模块。

封装时间日期查询能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolRetryPolicy
from app.capabilities.datetime import DateTimeCapability


class GetCurrentTimeInput(BaseModel):
    """获取当前时间的输入参数。"""
    timezone_or_city: str = Field(default='UTC', description='时区缩写（CST / EST / PST）或城市名（北京 / 纽约 / 东京），默认 UTC')


class GetCurrentTimeOutput(BaseModel):
    """当前时间输出。"""
    timezone: str
    datetime_str: str
    hour: int
    minute: int
    second: int
    weekday: int
    weekday_name: str


class GetDateInfoInput(BaseModel):
    """获取日期信息的输入参数。"""
    date_str: str = Field(default='', description='日期字符串（如 "2026-07-04"），为空则返回今天')


class GetDateInfoOutput(BaseModel):
    """日期信息输出。"""
    date_str: str
    year: int
    month: int
    day: int
    weekday: int
    weekday_name: str
    is_leap_year: bool
    day_of_year: int
    days_in_month: int


class GetCurrentTimeTool:
    """获取指定时区或城市的当前时间。"""

    name = 'get_current_time'
    version = 'v1'
    timeout_ms = 3_000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetCurrentTimeInput
    output_model = GetCurrentTimeOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetCurrentTimeInput, context) -> GetCurrentTimeOutput:
        """获取当前时间。"""
        cap = DateTimeCapability()
        result = cap.get_current_time(payload.timezone_or_city)
        return GetCurrentTimeOutput(
            timezone=result.timezone,
            datetime_str=result.datetime_str,
            hour=result.hour,
            minute=result.minute,
            second=result.second,
            weekday=result.weekday,
            weekday_name=result.weekday_name,
        )


class GetDateInfoTool:
    """获取指定日期的详细信息。"""

    name = 'get_date_info'
    version = 'v1'
    timeout_ms = 3_000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetDateInfoInput
    output_model = GetDateInfoOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetDateInfoInput, context) -> GetDateInfoOutput:
        """获取日期信息。"""
        cap = DateTimeCapability()
        result = cap.get_date_info(payload.date_str)
        return GetDateInfoOutput(
            date_str=result.date_str,
            year=result.year,
            month=result.month,
            day=result.day,
            weekday=result.weekday,
            weekday_name=result.weekday_name,
            is_leap_year=result.is_leap_year,
            day_of_year=result.day_of_year,
            days_in_month=result.days_in_month,
        )

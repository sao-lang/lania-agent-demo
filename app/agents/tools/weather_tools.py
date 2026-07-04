"""天气查询工具模块。

封装天气查询能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.weather import WeatherCapability


class GetCurrentWeatherInput(BaseModel):
    """获取当前天气的输入参数。"""
    location: str = Field(description='地点名称，如 "北京"、"Tokyo"、"New York"')
    units: str = Field(default='metric', description='温度单位：metric（摄氏 ℃）或 imperial（华氏 ℉）')


class GetCurrentWeatherOutput(BaseModel):
    """当前天气输出。"""
    location: str
    temperature: float
    feels_like: float
    humidity: int
    pressure: int
    description: str
    wind_speed: float
    wind_direction: int
    visibility: int


class GetWeatherForecastInput(BaseModel):
    """获取天气预报的输入参数。"""
    location: str = Field(description='地点名称')
    days: int = Field(default=5, ge=1, le=7, description='预报天数')
    units: str = Field(default='metric', description='温度单位：metric（摄氏）或 imperial（华氏）')


class ForecastDayOutput(BaseModel):
    """单日天气预报输出。"""
    date: str
    temp_max: float
    temp_min: float
    humidity: int
    description: str
    wind_speed: float
    pop: float


class GetWeatherForecastOutput(BaseModel):
    """天气预报输出。"""
    location: str
    forecast: list[ForecastDayOutput]


class GetCurrentWeatherTool:
    """获取指定地点的当前天气。"""

    name = 'get_current_weather'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetCurrentWeatherInput
    output_model = GetCurrentWeatherOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetCurrentWeatherInput, context) -> GetCurrentWeatherOutput:
        """执行天气查询。"""
        cap = self._get_capability(context)
        try:
            result = cap.get_current_weather(payload.location, payload.units)
        except (LookupError, ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='weather_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GetCurrentWeatherOutput(
            location=result.location,
            temperature=result.temperature,
            feels_like=result.feels_like,
            humidity=result.humidity,
            pressure=result.pressure,
            description=result.description,
            wind_speed=result.wind_speed,
            wind_direction=result.wind_direction,
            visibility=result.visibility,
        )

    def _get_capability(self, context) -> WeatherCapability:
        """从 context 中获取 WeatherCapability。"""
        services = getattr(context, 'services', None) or {}
        cap = services.get('weather')
        if cap is not None:
            return cap
        # 兜底：从 settings 创建
        from app.capabilities.weather import WeatherCapability
        api_key = getattr(context.settings, 'weather_api_key', '')
        return WeatherCapability(api_key=api_key)


class GetWeatherForecastTool:
    """获取指定地点的天气预报。"""

    name = 'get_weather_forecast'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetWeatherForecastInput
    output_model = GetWeatherForecastOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetWeatherForecastInput, context) -> GetWeatherForecastOutput:
        """执行天气预报查询。"""
        cap = self._get_capability(context)
        try:
            forecast = cap.get_forecast(payload.location, payload.days, payload.units)
        except (LookupError, ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='weather_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GetWeatherForecastOutput(
            location=payload.location,
            forecast=[
                ForecastDayOutput(
                    date=f.date,
                    temp_max=f.temp_max,
                    temp_min=f.temp_min,
                    humidity=f.humidity,
                    description=f.description,
                    wind_speed=f.wind_speed,
                    pop=f.pop,
                )
                for f in forecast
            ],
        )

    def _get_capability(self, context) -> WeatherCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('weather')
        if cap is not None:
            return cap
        from app.capabilities.weather import WeatherCapability
        api_key = getattr(context.settings, 'weather_api_key', '')
        return WeatherCapability(api_key=api_key)

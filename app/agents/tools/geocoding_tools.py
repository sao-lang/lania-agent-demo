"""地理编码工具模块。

封装地址解析能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.geocoding import GeocodingCapability


class GeocodeAddressInput(BaseModel):
    """正向地理编码的输入参数。"""
    address: str = Field(description='地址文本，如 "北京市朝阳区"、"Statue of Liberty"')
    limit: int = Field(default=5, ge=1, le=20, description='最大返回结果数')


class GeocodeResultOutput(BaseModel):
    """地理编码结果。"""
    display_name: str
    latitude: float
    longitude: float
    place_type: str
    importance: float


class GeocodeAddressOutput(BaseModel):
    """地址解析结果输出。"""
    query: str
    results: list[GeocodeResultOutput]
    total: int


class ReverseGeocodeInput(BaseModel):
    """逆向地理编码的输入参数。"""
    latitude: float = Field(description='纬度')
    longitude: float = Field(description='经度')


class ReverseGeocodeOutput(BaseModel):
    """逆向地理编码结果输出。"""
    display_name: str
    address: dict[str, str]
    latitude: float
    longitude: float
    place_type: str


class GeocodeAddressTool:
    """正向地理编码：将地址文本解析为经纬度坐标。"""

    name = 'geocode_address'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GeocodeAddressInput
    output_model = GeocodeAddressOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GeocodeAddressInput, context) -> GeocodeAddressOutput:
        """执行地址解析。"""
        cap = self._get_capability(context)
        try:
            results = cap.geocode(payload.address, payload.limit)
        except (LookupError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='geocoding_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GeocodeAddressOutput(
            query=payload.address,
            results=[
                GeocodeResultOutput(
                    display_name=r.display_name,
                    latitude=r.latitude,
                    longitude=r.longitude,
                    place_type=r.place_type,
                    importance=r.importance,
                )
                for r in results
            ],
            total=len(results),
        )

    def _get_capability(self, context) -> GeocodingCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('geocoding')
        if cap is not None:
            return cap
        return GeocodingCapability()


class ReverseGeocodeTool:
    """逆向地理编码：将经纬度坐标解析为地址文本。"""

    name = 'reverse_geocode'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ReverseGeocodeInput
    output_model = ReverseGeocodeOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: ReverseGeocodeInput, context) -> ReverseGeocodeOutput:
        """执行逆向地理编码。"""
        cap = self._get_capability(context)
        try:
            result = cap.reverse_geocode(payload.latitude, payload.longitude)
        except (LookupError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='geocoding_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return ReverseGeocodeOutput(
            display_name=result.display_name,
            address=result.address,
            latitude=result.latitude,
            longitude=result.longitude,
            place_type=result.place_type,
        )

    def _get_capability(self, context) -> GeocodingCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('geocoding')
        if cap is not None:
            return cap
        return GeocodingCapability()

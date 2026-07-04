"""汇率转换工具模块。

封装汇率转换能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.currency import CurrencyCapability


class ConvertCurrencyInput(BaseModel):
    """货币转换的输入参数。"""
    amount: float = Field(description='转换金额')
    from_currency: str = Field(description='源货币代码，如 USD / EUR / CNY')
    to_currency: str = Field(description='目标货币代码，如 USD / EUR / CNY')


class ConvertCurrencyOutput(BaseModel):
    """货币转换结果输出。"""
    amount: float
    from_currency: str
    to_currency: str
    result: float
    rate: float


class GetExchangeRatesInput(BaseModel):
    """获取汇率的输入参数。"""
    base_currency: str = Field(default='USD', description='基础货币代码，如 USD / EUR / CNY')


class ExchangeRateOutput(BaseModel):
    """汇率输出。"""
    currency: str
    rate: float


class GetExchangeRatesOutput(BaseModel):
    """汇率列表输出。"""
    base_currency: str
    rates: list[ExchangeRateOutput]
    total: int


class ConvertCurrencyTool:
    """货币转换。"""

    name = 'convert_currency'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ConvertCurrencyInput
    output_model = ConvertCurrencyOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: ConvertCurrencyInput, context) -> ConvertCurrencyOutput:
        """执行货币转换。"""
        cap = self._get_capability(context)
        try:
            result = cap.convert_currency(payload.amount, payload.from_currency, payload.to_currency)
        except (LookupError, ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='currency_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return ConvertCurrencyOutput(
            amount=result.amount,
            from_currency=result.from_currency,
            to_currency=result.to_currency,
            result=result.result,
            rate=result.rate,
        )

    def _get_capability(self, context) -> CurrencyCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('currency')
        if cap is not None:
            return cap
        return CurrencyCapability()


class GetExchangeRatesTool:
    """获取实时汇率。"""

    name = 'get_exchange_rates'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetExchangeRatesInput
    output_model = GetExchangeRatesOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetExchangeRatesInput, context) -> GetExchangeRatesOutput:
        """获取汇率。"""
        cap = self._get_capability(context)
        try:
            rates = cap.get_exchange_rates(payload.base_currency)
        except (LookupError, ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='currency_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GetExchangeRatesOutput(
            base_currency=payload.base_currency,
            rates=[ExchangeRateOutput(currency=r.currency, rate=r.rate) for r in rates],
            total=len(rates),
        )

    def _get_capability(self, context) -> CurrencyCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('currency')
        if cap is not None:
            return cap
        return CurrencyCapability()

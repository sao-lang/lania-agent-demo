"""金融数据工具模块。

封装股票行情查询能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.finance import FinanceCapability


class GetStockQuoteInput(BaseModel):
    """获取股票行情的输入参数。"""
    symbol: str = Field(description='股票代码，支持 sh600519 / 600519 / AAPL / US.AAPL 等格式')


class GetStockQuoteOutput(BaseModel):
    """股票实时行情输出。"""
    symbol: str
    name: str
    price: float
    change: float
    change_percent: float
    high: float
    low: float
    open: float
    prev_close: float
    volume: int


class GetHistoricalPricesInput(BaseModel):
    """获取历史股价的输入参数。"""
    symbol: str = Field(description='股票代码')
    period: str = Field(default='1mo', description='周期：5d / 1mo / 3mo / 6mo / 1y')


class HistoricalPriceOutput(BaseModel):
    """历史股价数据点。"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class GetHistoricalPricesOutput(BaseModel):
    """历史股价输出。"""
    symbol: str
    period: str
    prices: list[HistoricalPriceOutput]


class GetStockQuoteTool:
    """获取股票实时行情。"""

    name = 'get_stock_quote'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetStockQuoteInput
    output_model = GetStockQuoteOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetStockQuoteInput, context) -> GetStockQuoteOutput:
        """执行股票行情查询。"""
        cap = self._get_capability(context)
        try:
            q = cap.get_stock_quote(payload.symbol)
        except (LookupError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='finance_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GetStockQuoteOutput(
            symbol=q.symbol,
            name=q.name,
            price=q.price,
            change=q.change,
            change_percent=q.change_percent,
            high=q.high,
            low=q.low,
            open=q.open,
            prev_close=q.prev_close,
            volume=q.volume,
        )

    def _get_capability(self, context) -> FinanceCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('finance')
        if cap is not None:
            return cap
        return FinanceCapability()


class GetHistoricalPricesTool:
    """获取股票历史价格数据。"""

    name = 'get_historical_prices'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetHistoricalPricesInput
    output_model = GetHistoricalPricesOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetHistoricalPricesInput, context) -> GetHistoricalPricesOutput:
        """执行历史股价查询。"""
        cap = self._get_capability(context)
        try:
            prices = cap.get_historical_prices(payload.symbol, payload.period)
        except (LookupError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='finance_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GetHistoricalPricesOutput(
            symbol=payload.symbol,
            period=payload.period,
            prices=[
                HistoricalPriceOutput(
                    date=p.date, open=p.open, high=p.high,
                    low=p.low, close=p.close, volume=p.volume,
                )
                for p in prices
            ],
        )

    def _get_capability(self, context) -> FinanceCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('finance')
        if cap is not None:
            return cap
        return FinanceCapability()

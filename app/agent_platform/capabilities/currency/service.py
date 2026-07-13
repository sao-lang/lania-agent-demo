"""汇率转换能力实现。

通过 Frankfurter API（免费、无需 API Key）获取实时汇率数据。
"""

from __future__ import annotations
import httpx

from dataclasses import dataclass


@dataclass
class ExchangeRate:
    """汇率信息。"""
    currency: str
    rate: float


@dataclass
class ConversionResult:
    """货币转换结果。"""
    amount: float
    from_currency: str
    to_currency: str
    result: float
    rate: float


class CurrencyCapability:
    """汇率转换能力，支持实时汇率查询与货币转换。"""

    name = 'currency'

    def __init__(self, base_url: str = 'https://api.frankfurter.dev') -> None:
        self._base_url = base_url.rstrip('/')
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def get_exchange_rates(self, base_currency: str = 'USD') -> list[ExchangeRate]:
        """获取以指定货币为基础的汇率。

        Args:
            base_currency: 基础货币代码（如 USD, EUR, CNY）。

        Returns:
            ExchangeRate 列表。
        """
        import httpx
        client = self._get_client()
        try:
            resp = client.get(f'{self._base_url}/latest', params={'from': base_currency.upper()})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LookupError(f'currency not supported: {base_currency}') from exc
            raise ConnectionError(f'currency API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('currency API timed out') from exc

        rates = data.get('rates', {})
        return [
            ExchangeRate(currency=code, rate=rate)
            for code, rate in rates.items()
        ]

    def convert_currency(self, amount: float, from_currency: str, to_currency: str) -> ConversionResult:
        """货币转换。

        Args:
            amount: 转换金额。
            from_currency: 源货币代码。
            to_currency: 目标货币代码。

        Returns:
            ConversionResult 转换结果。
        """
        import httpx
        client = self._get_client()
        try:
            resp = client.get(
                f'{self._base_url}/latest',
                params={'from': from_currency.upper(), 'to': to_currency.upper()},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(f'currency API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('currency API timed out') from exc

        rates = data.get('rates', {})
        rate = rates.get(to_currency.upper())
        if rate is None:
            raise LookupError(f'currency not supported: {to_currency}')

        return ConversionResult(
            amount=amount,
            from_currency=from_currency.upper(),
            to_currency=to_currency.upper(),
            result=round(amount * rate, 2),
            rate=rate,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

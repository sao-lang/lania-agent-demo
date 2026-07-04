"""金融数据能力实现。

通过新浪财经免费接口获取 A 股/港股/美股行情数据，无需 API Key。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class StockQuote:
    """股票实时行情。"""
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
    amount: float
    timestamp: str


@dataclass
class HistoricalPrice:
    """历史股价数据点。"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


# 新浪财经接口前缀
_SINA_API = 'https://hq.sinajs.cn/list='

# 市场前缀映射
_MARKET_PREFIX = {
    'sh': 'sh', 'sz': 'sz', 'bj': 'bj',
    'us': 'gb_', 'hk': 'hk',
}


class FinanceCapability:
    """金融数据能力，支持股票行情查询。"""

    name = 'finance'

    def __init__(self) -> None:
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=15.0, headers={'Referer': 'https://finance.sina.com.cn'})
        return self._client

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """规范化股票代码，自动识别市场前缀。"""
        symbol = symbol.strip().upper()
        # 已带前缀的格式如 sh600519, sz000001, usAAPL, hk00700
        for prefix in ('SH', 'SZ', 'BJ', 'US', 'HK'):
            if symbol.startswith(prefix):
                return symbol.lower()
        # 纯数字代码，按长度判断市场
        if re.match(r'^\d{6}$', symbol):
            if symbol.startswith('6') or symbol.startswith('9'):
                return f'sh{symbol}'
            return f'sz{symbol}'
        # 美股代码（字母）
        if re.match(r'^[A-Z]{1,8}$', symbol):
            return f'gb_{symbol}'
        return symbol.lower()

    def get_stock_quote(self, symbol: str) -> StockQuote:
        """获取股票实时行情。

        Args:
            symbol: 股票代码，支持 sh600519 / 600519 / AAPL / US.AAPL 等格式。

        Returns:
            StockQuote 行情数据。
        """
        import httpx
        client = self._get_client()
        normalized = self._normalize_symbol(symbol)
        try:
            resp = client.get(f'{_SINA_API}{normalized}')
            resp.raise_for_status()
            text = resp.text
        except httpx.TimeoutException as exc:
            raise TimeoutError('finance API timed out') from exc

        # 解析新浪 CSV 格式返回
        match = re.search(r'"(.*)"', text)
        if not match:
            raise LookupError(f'symbol not found: {symbol}')

        parts = match.group(1).split(',')
        if len(parts) < 30:
            raise LookupError(f'invalid data for symbol: {symbol}')

        name = parts[0]
        open_price = float(parts[1]) if parts[1] else 0.0
        prev_close = float(parts[2]) if parts[2] else 0.0
        price = float(parts[3]) if parts[3] else 0.0
        high = float(parts[4]) if parts[4] else 0.0
        low = float(parts[5]) if parts[5] else 0.0
        volume = int(parts[8]) if parts[8] else 0
        amount = float(parts[9]) if parts[9] else 0.0
        change = round(price - prev_close, 2)
        change_percent = round((change / prev_close * 100), 2) if prev_close else 0.0

        return StockQuote(
            symbol=symbol,
            name=name,
            price=price,
            change=change,
            change_percent=change_percent,
            high=high,
            low=low,
            open=open_price,
            prev_close=prev_close,
            volume=volume,
            amount=amount,
            timestamp='',
        )

    def get_historical_prices(self, symbol: str, period: str = '1mo') -> list[HistoricalPrice]:
        """获取历史股价数据（通过新浪财经）。

        Args:
            symbol: 股票代码。
            period: 周期，如 5d / 1mo / 3mo / 6mo / 1y。

        Returns:
            HistoricalPrice 列表。
        """
        import httpx
        client = self._get_client()
        normalized = self._normalize_symbol(symbol)

        period_map = {'5d': 5, '1mo': 30, '3mo': 90, '6mo': 180, '1y': 365}
        days = period_map.get(period, 30)

        try:
            url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={normalized}&scale=240&ma=no&datalen={days}'
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError('finance API timed out') from exc

        if not data or not isinstance(data, list):
            raise LookupError(f'no historical data for symbol: {symbol}')

        results: list[HistoricalPrice] = []
        for item in data:
            results.append(HistoricalPrice(
                date=item.get('date', ''),
                open=float(item.get('open', 0)),
                high=float(item.get('high', 0)),
                low=float(item.get('low', 0)),
                close=float(item.get('close', 0)),
                volume=int(item.get('volume', 0)),
            ))
        return results

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

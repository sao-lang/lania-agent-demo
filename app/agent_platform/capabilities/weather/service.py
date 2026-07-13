"""天气查询能力实现。

通过 OpenWeatherMap API 获取实时天气与预报数据。
"""

from __future__ import annotations
import httpx

from dataclasses import dataclass


@dataclass
class CurrentWeather:
    """当前天气数据。"""
    location: str
    temperature: float
    feels_like: float
    humidity: int
    pressure: int
    description: str
    wind_speed: float
    wind_direction: int
    visibility: int
    uv_index: float | None = None


@dataclass
class ForecastDay:
    """单日天气预报。"""
    date: str
    temp_max: float
    temp_min: float
    humidity: int
    description: str
    wind_speed: float
    pop: float  # 降水概率 0-1


class WeatherCapability:
    """天气查询能力，支持实时天气与天气预报。"""

    name = 'weather'

    def __init__(self, api_key: str = '', base_url: str = 'https://api.openweathermap.org/data/2.5') -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def get_current_weather(self, location: str, units: str = 'metric') -> CurrentWeather:
        """获取指定地点的当前天气。

        Args:
            location: 地点名称（如 "Beijing"、"Tokyo"、"London"）。
            units: 温度单位，metric（摄氏）/ imperial（华氏）。

        Returns:
            CurrentWeather 数据对象。
        """
        import httpx
        client = self._get_client()
        params: dict[str, str] = {'q': location, 'units': units}
        if self._api_key:
            params['appid'] = self._api_key
        try:
            resp = client.get(f'{self._base_url}/weather', params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LookupError(f'location not found: {location}') from exc
            raise ConnectionError(f'weather API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('weather API timed out') from exc

        return CurrentWeather(
            location=location,
            temperature=data['main']['temp'],
            feels_like=data['main']['feels_like'],
            humidity=data['main']['humidity'],
            pressure=data['main']['pressure'],
            description=data['weather'][0]['description'],
            wind_speed=data['wind']['speed'],
            wind_direction=data['wind']['deg'],
            visibility=data.get('visibility', 0),
        )

    def get_forecast(self, location: str, days: int = 5, units: str = 'metric') -> list[ForecastDay]:
        """获取指定地点的天气预报。

        Args:
            location: 地点名称。
            days: 预报天数（1-7）。
            units: 温度单位。

        Returns:
            ForecastDay 列表。
        """
        import httpx
        client = self._get_client()
        params: dict[str, str] = {'q': location, 'units': units, 'cnt': str(min(max(days, 1), 7))}
        if self._api_key:
            params['appid'] = self._api_key
        try:
            resp = client.get(f'{self._base_url}/forecast', params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LookupError(f'location not found: {location}') from exc
            raise ConnectionError(f'weather API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('weather API timed out') from exc

        results: list[ForecastDay] = []
        seen_dates: set[str] = set()
        for item in data.get('list', []):
            date_str = item['dt_txt'][:10]
            if date_str in seen_dates:
                continue
            seen_dates.add(date_str)
            results.append(ForecastDay(
                date=date_str,
                temp_max=item['main']['temp_max'],
                temp_min=item['main']['temp_min'],
                humidity=item['main']['humidity'],
                description=item['weather'][0]['description'],
                wind_speed=item['wind']['speed'],
                pop=item.get('pop', 0.0),
            ))
            if len(results) >= days:
                break
        return results

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

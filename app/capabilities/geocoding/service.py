"""地理编码能力实现。

通过 Nominatim（OpenStreetMap 免费 API）进行地址解析与逆地理编码。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GeocodingResult:
    """正向地理编码结果。"""
    display_name: str
    latitude: float
    longitude: float
    place_type: str
    importance: float


@dataclass
class ReverseGeocodingResult:
    """逆向地理编码结果。"""
    display_name: str
    address: dict[str, str]
    latitude: float
    longitude: float
    place_type: str


class GeocodingCapability:
    """地理编码能力，支持地址→坐标和坐标→地址。"""

    name = 'geocoding'

    def __init__(self, user_agent: str = 'LaniaAgentDemo/1.0') -> None:
        self._user_agent = user_agent
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            self._client = httpx.Client(
                timeout=15.0,
                headers={'User-Agent': self._user_agent},
            )
        return self._client

    def geocode(self, address: str, limit: int = 5) -> list[GeocodingResult]:
        """正向地理编码：地址 → 经纬度。

        Args:
            address: 地址文本。
            limit: 最大返回结果数。

        Returns:
            GeocodingResult 列表。
        """
        import httpx
        client = self._get_client()
        try:
            resp = client.get(
                'https://nominatim.openstreetmap.org/search',
                params={'q': address, 'format': 'json', 'limit': min(limit, 20), 'addressdetails': '1'},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError('geocoding API timed out') from exc

        if not data:
            raise LookupError(f'address not found: {address}')

        return [
            GeocodingResult(
                display_name=item.get('display_name', ''),
                latitude=float(item['lat']),
                longitude=float(item['lon']),
                place_type=item.get('type', ''),
                importance=float(item.get('importance', 0)),
            )
            for item in data
        ]

    def reverse_geocode(self, latitude: float, longitude: float) -> ReverseGeocodingResult:
        """逆向地理编码：经纬度 → 地址。

        Args:
            latitude: 纬度。
            longitude: 经度。

        Returns:
            ReverseGeocodingResult 地址信息。
        """
        import httpx
        client = self._get_client()
        try:
            resp = client.get(
                'https://nominatim.openstreetmap.org/reverse',
                params={'lat': str(latitude), 'lon': str(longitude), 'format': 'json', 'addressdetails': '1'},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError('geocoding API timed out') from exc

        if 'error' in data:
            raise LookupError(f'coordinates not found: {latitude}, {longitude}')

        return ReverseGeocodingResult(
            display_name=data.get('display_name', ''),
            address=data.get('address', {}),
            latitude=latitude,
            longitude=longitude,
            place_type=data.get('type', ''),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

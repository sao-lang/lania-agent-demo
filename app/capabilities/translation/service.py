"""翻译能力实现。

通过 LibreTranslate 免费 API（无需 API Key）进行文本翻译与语言检测。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranslationResult:
    """翻译结果。"""
    translated_text: str
    detected_source_language: str
    source_text: str


# 常见语言代码与中文名称映射
_LANGUAGE_NAMES: dict[str, str] = {
    'zh': '中文', 'en': '英语', 'ja': '日语', 'ko': '韩语',
    'fr': '法语', 'de': '德语', 'es': '西班牙语', 'pt': '葡萄牙语',
    'ru': '俄语', 'ar': '阿拉伯语', 'it': '意大利语', 'nl': '荷兰语',
    'pl': '波兰语', 'tr': '土耳其语', 'vi': '越南语', 'th': '泰语',
}


class TranslationCapability:
    """翻译能力，支持文本翻译和语言检测。"""

    name = 'translation'

    def __init__(self, base_url: str = 'https://libretranslate.com') -> None:
        self._base_url = base_url.rstrip('/')
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=30.0)
        return self._client

    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str = '',
    ) -> TranslationResult:
        """翻译文本。

        Args:
            text: 待翻译文本。
            target_language: 目标语言代码（如 zh, en, ja）。
            source_language: 源语言代码，为空则自动检测。

        Returns:
            TranslationResult 翻译结果。
        """
        import httpx
        client = self._get_client()
        payload: dict[str, str] = {
            'q': text,
            'target': target_language,
        }
        if source_language:
            payload['source'] = source_language

        try:
            resp = client.post(f'{self._base_url}/translate', json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(f'translation API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('translation API timed out') from exc

        detected = data.get('detectedLanguage', {})
        return TranslationResult(
            translated_text=data.get('translatedText', ''),
            detected_source_language=detected.get('language', source_language or 'unknown'),
            source_text=text,
        )

    def detect_language(self, text: str) -> dict[str, str]:
        """检测文本的语言。

        Args:
            text: 待检测文本。

        Returns:
            包含 language 和 confidence 的字典。
        """
        import httpx
        client = self._get_client()
        try:
            resp = client.post(f'{self._base_url}/detect', json={'q': text})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(f'translation API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('translation API timed out') from exc

        if isinstance(data, list) and data:
            return {
                'language': data[0].get('language', 'unknown'),
                'confidence': str(data[0].get('confidence', 0)),
            }
        return {'language': 'unknown', 'confidence': '0'}

    @staticmethod
    def get_supported_languages() -> list[dict[str, str]]:
        """获取支持的语言列表。"""
        return [
            {'code': code, 'name': name}
            for code, name in _LANGUAGE_NAMES.items()
        ]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

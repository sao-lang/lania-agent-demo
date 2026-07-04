"""网页抓取能力实现。

通过 httpx 获取网页内容并提取正文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FetchedPage:
    """抓取的网页内容。"""
    url: str
    title: str
    text_content: str
    html_preview: str
    content_length: int
    status_code: int


class UrlFetchCapability:
    """网页抓取能力，支持获取网页正文内容。"""

    name = 'url_fetch'

    def __init__(self) -> None:
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            self._client = httpx.Client(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/125.0.0.0 Safari/537.36'
                    ),
                },
            )
        return self._client

    def fetch_page(self, url: str, max_chars: int = 10000) -> FetchedPage:
        """获取网页内容并提取正文文本。

        Args:
            url: 网页 URL。
            max_chars: 最大返回字符数。

        Returns:
            FetchedPage 网页内容。
        """
        import httpx
        client = self._get_client()
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(f'HTTP {exc.response.status_code}: {url}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError(f'timeout fetching: {url}') from exc

        html = resp.text
        title = self._extract_title(html)
        text = self._extract_text(html)
        text = text[:max_chars]

        return FetchedPage(
            url=url,
            title=title,
            text_content=text,
            html_preview=html[:500],
            content_length=len(text),
            status_code=resp.status_code,
        )

    @staticmethod
    def _extract_title(html: str) -> str:
        """从 HTML 中提取标题。"""
        match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return ''

    @staticmethod
    def _extract_text(html: str) -> str:
        """从 HTML 中提取纯文本。"""
        # 移除 script 和 style 标签
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.IGNORECASE | re.DOTALL)
        # 替换块级标签为换行
        text = re.sub(r'</?(?:div|p|br|h[1-6]|li|tr|blockquote|section)[^>]*>', '\n', text, flags=re.IGNORECASE)
        # 移除其他标签
        text = re.sub(r'<[^>]+>', '', text)
        # 解码 HTML 实体
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
        # 清理多余空白
        text = re.sub(r'\n\s*\n', '\n', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

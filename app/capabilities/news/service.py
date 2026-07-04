"""新闻聚合能力实现。

通过 NewsAPI / GNews 等免费接口获取最新新闻。
也支持通过 DuckDuckGo 搜索获取新闻类结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class NewsArticle:
    """新闻文章。"""
    title: str
    description: str
    url: str
    source: str
    published_at: str
    content: str = ''


class NewsCapability:
    """新闻聚合能力，支持搜索和获取最新新闻。"""

    name = 'news'

    def __init__(self, api_key: str = '', base_url: str = 'https://newsapi.org/v2') -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            import httpx
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            if self._api_key:
                headers['X-Api-Key'] = self._api_key
            self._client = httpx.Client(timeout=15.0, headers=headers)
        return self._client

    def get_latest_news(self, query: str = '', language: str = 'zh', max_results: int = 10) -> list[NewsArticle]:
        """获取最新新闻。

        Args:
            query: 搜索关键词，为空则返回热门新闻。
            language: 语言代码（zh / en 等）。
            max_results: 最大结果数。

        Returns:
            NewsArticle 列表。
        """
        if self._api_key:
            return self._search_via_newsapi(query, language, max_results)
        return self._search_via_duckduckgo(query, max_results)

    def search_news(self, query: str, language: str = 'zh', max_results: int = 10) -> list[NewsArticle]:
        """搜索新闻。

        Args:
            query: 搜索关键词。
            language: 语言代码。
            max_results: 最大结果数。

        Returns:
            NewsArticle 列表。
        """
        return self.get_latest_news(query=query, language=language, max_results=max_results)

    def _search_via_newsapi(self, query: str, language: str, max_results: int) -> list[NewsArticle]:
        import httpx
        client = self._get_client()
        params: dict[str, str | int] = {
            'language': language,
            'pageSize': min(max_results, 100),
            'sortBy': 'publishedAt',
        }
        endpoint = 'top-headlines'
        if query:
            endpoint = 'everything'
            params['q'] = query
        else:
            params['country'] = language if len(language) == 2 else 'us'

        try:
            resp = client.get(f'{self._base_url}/{endpoint}', params=params)  # type: ignore[arg-type]
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(f'news API error: {exc.response.status_code}') from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError('news API timed out') from exc

        articles = data.get('articles', [])
        return [
            NewsArticle(
                title=art.get('title', ''),
                description=art.get('description', '') or '',
                url=art.get('url', ''),
                source=art.get('source', {}).get('name', ''),
                published_at=art.get('publishedAt', ''),
                content=art.get('content', '') or '',
            )
            for art in articles[:max_results]
        ]

    def _search_via_duckduckgo(self, query: str, max_results: int) -> list[NewsArticle]:
        import httpx
        client = self._get_client()
        search_term = query if query else 'latest news'
        try:
            resp = client.post(
                'https://lite.duckduckgo.com/lite/',
                data={'q': search_term},
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError('search timed out') from exc

        return self._parse_ddg_html(resp.text, max_results)

    @staticmethod
    def _parse_ddg_html(html: str, max_results: int) -> list[NewsArticle]:
        from html.parser import HTMLParser

        class _LinkCollector(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.results: list[NewsArticle] = []
                self._in_result = False
                self._current: dict[str, str] = {}
                self._tag_stack: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                self._tag_stack.append(tag)
                if tag == 'a':
                    for name, value in attrs:
                        if name == 'href' and value and '//' in value:
                            self._current.setdefault('url', value)
                if tag == 'td' and self._tag_stack.count('td') <= 3:
                    pass

            def handle_data(self, data: str) -> None:
                text = data.strip()
                if not text:
                    return
                if len(self._tag_stack) >= 2 and self._tag_stack[-1] == 'a':
                    if 'url' in self._current and 'title' not in self._current:
                        self._current['title'] = text
                    elif 'url' in self._current and 'title' in self._current and 'description' not in self._current:
                        self._current['description'] = text
                        self._finalize()

            def _finalize(self) -> None:
                if 'title' in self._current and 'url' in self._current:
                    self.results.append(NewsArticle(
                        title=self._current.get('title', ''),
                        description=self._current.get('description', ''),
                        url=self._current.get('url', ''),
                        source='DuckDuckGo',
                        published_at=datetime.now(timezone.utc).isoformat(),
                    ))
                self._current = {}

            def handle_endtag(self, tag: str) -> None:
                if self._tag_stack:
                    self._tag_stack.pop()

        collector = _LinkCollector()
        collector.feed(html)
        return collector.results[:max_results]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

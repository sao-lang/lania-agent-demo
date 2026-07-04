"""联网搜索 Capability 实现。

搜索互联网 → 抓取页面内容 → LLM 生成回答。
支持通过配置的搜索 API（如 SearXNG、Google Custom Search）或直接 URL 抓取。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from app.models.agent import AgentEvent

# 默认搜索请求头，模拟浏览器行为
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 搜索 API 配置
_SEARCH_API_URL = "https://lite.duckduckgo.com/lite/"
_USE_DUCKDUCKGO_LITE = True


class WebSearchCapability:
    """联网搜索能力。

    搜索网络、抓取页面内容、使用 LLM 整合回答。
    """

    name = "web_search"

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=_DEFAULT_HEADERS,
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def execute(
        self,
        message: str,
        context: dict[str, Any],
    ) -> list[AgentEvent]:
        """执行联网搜索。

        Args:
            message: 搜索查询或问题。
            context: 执行上下文（含 llm 等）。

        Returns:
            Agent 事件列表。
        """
        events: list[AgentEvent] = []
        llm = context.get("llm") or self._llm

        # 1. 搜索网络
        events.append(AgentEvent.step_start(1, "搜索网络", "查找相关信息"))
        search_results = await self._search_web(message)
        if not search_results:
            events.append(AgentEvent.delta("未找到搜索结果。"))
            events.append(AgentEvent.completed())
            return events

        events.append(
            AgentEvent.delta(f"找到 {len(search_results)} 个结果\n")
        )
        events.append(AgentEvent.step_end(1, "completed"))

        # 2. 抓取页面内容（取前 3 个结果）
        events.append(AgentEvent.step_start(2, "读取页面", "获取详细内容"))
        page_contents: list[dict[str, str]] = []
        for i, result in enumerate(search_results[:3]):
            content = await self._fetch_page(result["url"])
            if content:
                page_contents.append({
                    "title": result.get("title", ""),
                    "url": result["url"],
                    "content": content[:3000],  # 截断过长内容
                })
                label = result.get("title", result["url"])
                events.append(
                    AgentEvent.delta(f"  ✓ {label}\n")
                )
            else:
                label = result.get("title", result["url"])
                events.append(
                    AgentEvent.delta(f"  ✗ {label}\n")
                )
        events.append(AgentEvent.step_end(2, "completed"))

        # 3. LLM 整合回答
        if not llm:
            events.append(AgentEvent.delta("未配置 LLM，返回原始搜索结果。\n"))
            for r in search_results[:5]:
                events.append(
                    AgentEvent.delta(f"- {r.get('title', '')}: {r['url']}\n")
                )
            events.append(AgentEvent.completed())
            return events

        events.append(AgentEvent.step_start(3, "分析总结", "LLM 整合信息"))
        answer = await self._generate_answer(llm, message, page_contents)
        events.append(AgentEvent.delta(answer if answer else "分析完成。"))
        events.append(AgentEvent.step_end(3, "completed"))

        # 附上来源链接
        sources = "\n\n**来源：**\n" + "\n".join(
            f"- [{r.get('title', '链接')}]({r['url']})"
            for r in search_results[:5]
        )
        events.append(AgentEvent.delta(sources))

        events.append(AgentEvent.completed())
        return events

    async def _search_web(self, query: str) -> list[dict[str, str]]:
        """搜索网络，返回结果列表。"""
        results: list[dict[str, str]] = []

        try:
            client = await self._get_client()

            if _USE_DUCKDUCKGO_LITE:
                # 使用 DuckDuckGo Lite 搜索（无 API Key 要求）
                response = await client.post(
                    _SEARCH_API_URL,
                    data={"q": query},
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                )
                if response.status_code == 200:
                    results = self._parse_duckduckgo_lite(response.text)
        except Exception:
            return []

        # 兜底：如果没有搜索结果，尝试直接 URL 抓取
        if not results:
            results = await self._fallback_search(query)

        return results

    def _parse_duckduckgo_lite(self, html: str) -> list[dict[str, str]]:
        """解析 DuckDuckGo Lite 的 HTML 搜索结果。"""
        results: list[dict[str, str]] = []

        # 简单的正则解析
        # DuckDuckGo Lite 结果结构：
        # <a rel="nofollow" href="url" class="result-link">title</a>
        link_pattern = re.compile(
            r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*'
            r'class="result-link"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<a[^>]*class="result-snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links):
            snippet = snippets[i] if i < len(snippets) else ""
            # 清理 HTML 标签
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            title = re.sub(r"<[^>]+>", "", title).strip()
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
            })

        return results

    async def _fallback_search(self, query: str) -> list[dict[str, str]]:
        """兜底搜索：尝试直接构造搜索 URL 抓取。"""
        results: list[dict[str, str]] = []
        search_urls = [
            f"https://html.duckduckgo.com/html/?q={quote(query)}",
        ]

        client = await self._get_client()
        for url in search_urls:
            try:
                response = await client.get(url, timeout=15.0)
                if response.status_code == 200:
                    html = response.text
                    # 简单提取链接和标题
                    link_pattern = re.compile(
                        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>'
                        r'(.*?)</a>', re.DOTALL,
                    )
                    links = link_pattern.findall(html)
                    for href, title in links[:8]:
                        title_clean = re.sub(r"<[^>]+>", "", title).strip()
                        results.append({
                            "title": title_clean,
                            "url": href,
                            "snippet": "",
                        })
                    if results:
                        break
            except Exception:
                continue

        return results

    async def _fetch_page(self, url: str) -> str | None:
        """抓取单个页面的文本内容。"""
        try:
            client = await self._get_client()
            response = await client.get(url, timeout=20.0)
            if response.status_code != 200:
                return None

            html = response.text
            # 提取纯文本
            text = self._html_to_text(html)
            # 截断并返回
            return text[:5000].strip()
        except Exception:
            return None

    def _html_to_text(self, html: str) -> str:
        """将 HTML 转换为纯文本。"""
        # 移除 script 和 style 标签
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

        # 移除标签，保留文本
        text = re.sub(r"<[^>]+>", " ", html)

        # 合并空白字符
        text = re.sub(r"\s+", " ", text)

        # 按句分割去重（简单去重）
        lines = [line.strip() for line in text.split(".") if line.strip()]
        seen = set()
        unique_lines = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                unique_lines.append(line)

        return ". ".join(unique_lines[:100])

    async def _generate_answer(
        self,
        llm: Any,
        query: str,
        pages: list[dict[str, str]],
    ) -> str | None:
        """使用 LLM 根据搜索结果生成回答。"""
        if not pages:
            return "未找到相关页面内容。"

        page_text = "\n\n".join(
            f"## {p['title']}\n来源: {p['url']}\n内容: {p['content'][:2000]}"
            for p in pages
        )

        prompt = (
            f"你是一个联网搜索助手。用户提问：{query}\n\n"
            f"以下是搜索结果：\n\n{page_text}\n\n"
            f"请根据以上搜索结果，用中文回答用户的问题。\n"
            f"要求：\n"
            f"1. 基于搜索结果，不要编造信息\n"
            f"2. 引用来源（标注 URL）\n"
            f"3. 如果搜索结果不足以回答问题，请明确说明\n"
            f"4. 回答简洁有条理\n"
        )

        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            if hasattr(response, "choices"):
                return response.choices[0].message.content
            return str(response)
        except Exception:
            return "LLM 调用失败，返回原始搜索结果。"

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client:
            await self._client.aclose()
            self._client = None

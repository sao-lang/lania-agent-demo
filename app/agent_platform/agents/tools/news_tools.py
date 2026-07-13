"""新闻查询工具模块�?

封装新闻聚合能力�?LLM 可调用的工具函数�?
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent_platform.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.agent_platform.capabilities.news import NewsCapability


class GetLatestNewsInput(BaseModel):
    """获取最新新闻的输入参数�?""
    query: str = Field(default='', description='搜索关键词，为空则返回热门新�?)
    language: str = Field(default='zh', description='语言代码（zh / en 等）')
    max_results: int = Field(default=10, ge=1, le=50, description='最大返回条�?)


class NewsArticleOutput(BaseModel):
    """新闻文章输出�?""
    title: str
    description: str
    url: str
    source: str
    published_at: str


class GetLatestNewsOutput(BaseModel):
    """最新新闻输出�?""
    articles: list[NewsArticleOutput]
    total: int


class SearchNewsInput(BaseModel):
    """搜索新闻的输入参数�?""
    query: str = Field(min_length=1, description='搜索关键�?)
    language: str = Field(default='zh', description='语言代码')
    max_results: int = Field(default=10, ge=1, le=50, description='最大返回条�?)


class SearchNewsOutput(BaseModel):
    """新闻搜索结果输出�?""
    query: str
    articles: list[NewsArticleOutput]
    total: int


class GetLatestNewsTool:
    """获取最新新闻�?""

    name = 'get_latest_news'
    version = 'v1'
    timeout_ms = 20_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = GetLatestNewsInput
    output_model = GetLatestNewsOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: GetLatestNewsInput, context) -> GetLatestNewsOutput:
        """获取最新新闻�?""
        cap = self._get_capability(context)
        try:
            articles = cap.get_latest_news(
                query=payload.query,
                language=payload.language,
                max_results=payload.max_results,
            )
        except (ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='news_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return GetLatestNewsOutput(
            articles=[self._to_output(a) for a in articles],
            total=len(articles),
        )

    def _get_capability(self, context) -> NewsCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('news')
        if cap is not None:
            return cap
        api_key = getattr(context.settings, 'news_api_key', '')
        return NewsCapability(api_key=api_key)

    @staticmethod
    def _to_output(a) -> NewsArticleOutput:
        return NewsArticleOutput(
            title=a.title,
            description=a.description,
            url=a.url,
            source=a.source,
            published_at=a.published_at,
        )


class SearchNewsTool:
    """搜索新闻�?""

    name = 'search_news'
    version = 'v1'
    timeout_ms = 20_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = SearchNewsInput
    output_model = SearchNewsOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: SearchNewsInput, context) -> SearchNewsOutput:
        """搜索新闻�?""
        cap = self._get_capability(context)
        try:
            articles = cap.search_news(
                query=payload.query,
                language=payload.language,
                max_results=payload.max_results,
            )
        except (ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='news_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return SearchNewsOutput(
            query=payload.query,
            articles=[GetLatestNewsTool._to_output(a) for a in articles],
            total=len(articles),
        )

    def _get_capability(self, context) -> NewsCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('news')
        if cap is not None:
            return cap
        api_key = getattr(context.settings, 'news_api_key', '')
        return NewsCapability(api_key=api_key)

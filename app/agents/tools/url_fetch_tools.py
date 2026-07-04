"""网页抓取工具模块。

封装网页内容获取能力为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.url_fetch import UrlFetchCapability


class FetchWebpageInput(BaseModel):
    """获取网页内容的输入参数。"""
    url: str = Field(description='网页 URL')
    max_chars: int = Field(default=10_000, ge=500, le=100_000, description='最大返回字符数')


class FetchWebpageOutput(BaseModel):
    """网页内容输出。"""
    url: str
    title: str
    text_content: str
    content_length: int
    status_code: int


class FetchWebpageTool:
    """获取网页内容并提取正文文本。"""

    name = 'fetch_webpage'
    version = 'v1'
    timeout_ms = 30_000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=1000)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = FetchWebpageInput
    output_model = FetchWebpageOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: FetchWebpageInput, context) -> FetchWebpageOutput:
        """获取网页内容。"""
        cap = self._get_capability(context)
        try:
            page = cap.fetch_page(payload.url, payload.max_chars)
        except (ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='fetch_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return FetchWebpageOutput(
            url=page.url,
            title=page.title,
            text_content=page.text_content,
            content_length=page.content_length,
            status_code=page.status_code,
        )

    def _get_capability(self, context) -> UrlFetchCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('url_fetch')
        if cap is not None:
            return cap
        return UrlFetchCapability()

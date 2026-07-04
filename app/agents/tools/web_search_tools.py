"""联网搜索工具模块。

封装 WebSearchCapability 为 LLM 可调用的工具函数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.web_search import WebSearchCapability


class WebSearchInput(BaseModel):
    """联网搜索的输入参数。"""
    query: str = Field(description='搜索查询内容')
    max_results: int = Field(default=5, ge=1, le=10, description='最大返回结果数')


class WebSearchResultItem(BaseModel):
    """搜索结果条目。"""
    title: str
    url: str
    snippet: str = ''


class WebSearchOutput(BaseModel):
    """联网搜索结果输出。"""
    query: str
    results: list[WebSearchResultItem]
    total: int
    answer: str = ''


class WebSearchTool:
    """搜索互联网获取信息。"""

    name = 'web_search'
    version = 'v1'
    timeout_ms = 30_000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=1000)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = WebSearchInput
    output_model = WebSearchOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: WebSearchInput, context) -> WebSearchOutput:
        """执行联网搜索。"""
        cap = WebSearchCapability(llm=getattr(context, 'llm', None))
        try:
            import asyncio
            events = asyncio.run(cap.execute(payload.query, {'llm': getattr(context, 'llm', None)}))
        except (ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='web_search_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc

        # 从 events 中提取结果
        results: list[WebSearchResultItem] = []
        answer = ''
        for event in events:
            if hasattr(event, 'delta') and event.delta:
                answer += event.delta
            # 尝试从搜索结果中提取结构化数据
            if hasattr(event, 'data') and event.data:
                if isinstance(event.data, dict) and 'results' in event.data:
                    for r in event.data['results']:
                        results.append(WebSearchResultItem(
                            title=r.get('title', ''),
                            url=r.get('url', ''),
                            snippet=r.get('snippet', ''),
                        ))

        return WebSearchOutput(
            query=payload.query,
            results=results[:payload.max_results],
            total=len(results),
            answer=answer,
        )

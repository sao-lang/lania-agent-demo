"""RAG 系统查询工作流事件模块。

提供 SSE 事件构造函数，把查询工作流中的开始、改写、检索、答案生成和完成等状态
转换为统一的事件协议。与 ``app/workflows/query_events.py`` 功能一致，
但仅依赖 rag_system 自有类型。
"""

from __future__ import annotations

from typing import Any

from app.rag_system.models.query import QueryResponse

SSEEvent = dict[str, Any]


def make_event(event: str, **data: Any) -> SSEEvent:
    """构造与现有 API 协议兼容的 SSE 事件对象。"""
    return {'event': event, 'data': data}


def append_event(events: list[SSEEvent], event: str, **data: Any) -> list[SSEEvent]:
    """在事件列表末尾追加一条标准 SSE 事件。"""
    return [*events, make_event(event, **data)]


def append_start_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``start`` 事件。"""
    return append_event(events, 'start', **data)


def append_step_started_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``step_started`` 事件。"""
    return append_event(events, 'step_started', **data)


def append_step_completed_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``step_completed`` 事件。"""
    return append_event(events, 'step_completed', **data)


def append_step_failed_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``step_failed`` 事件。"""
    return append_event(events, 'step_failed', **data)


def append_checkpoint_created_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``checkpoint_created`` 事件。"""
    return append_event(events, 'checkpoint_created', **data)


def append_rewrite_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``rewrite`` 事件。"""
    return append_event(events, 'rewrite', **data)


def append_multi_rewrite_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``multi_rewrite`` 事件。"""
    return append_event(events, 'multi_rewrite', **data)


def append_multi_query_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``multi_query`` 事件。"""
    return append_event(events, 'multi_query', **data)


def append_hyde_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``hyde`` 事件。"""
    return append_event(events, 'hyde', **data)


def append_cache_hit_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``cache_hit`` 事件。"""
    return append_event(events, 'cache_hit', **data)


def append_retrieval_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``retrieval`` 事件。"""
    return append_event(events, 'retrieval', **data)


def append_citation_ready_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``citation_ready`` 事件。"""
    return append_event(events, 'citation_ready', **data)


def append_answer_started_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``answer_started`` 事件。"""
    return append_event(events, 'answer_started', **data)


def append_corrective_check_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``corrective_check`` 事件。"""
    return append_event(events, 'corrective_check', **data)


def append_delta_events(events: list[SSEEvent], deltas: list[str]) -> list[SSEEvent]:
    """批量追加 ``delta`` 事件。"""
    current = events
    for delta in deltas:
        current = append_event(current, 'delta', delta=delta)
    return current


def append_answer_completed_event(events: list[SSEEvent], **data: Any) -> list[SSEEvent]:
    """追加 ``answer_completed`` 事件。"""
    return append_event(events, 'answer_completed', **data)


def append_done_event(events: list[SSEEvent], response: QueryResponse) -> list[SSEEvent]:
    """追加 ``done`` 事件。

    Args:
        events: 当前事件列表。
        response: 最终查询响应对象。

    Returns:
        包含序列化最终响应的事件列表。
    """
    return append_event(events, 'done', response=response.model_dump(mode='json'))


def make_error_event(code: str, message: str) -> SSEEvent:
    """构造 ``error`` 事件。"""
    return make_event('error', code=code, message=message)

"""查询接口模块。

负责承接 HTTP 查询请求，将请求模型转交给 `QueryService`，并在流式场景下把内部事件
编码成标准 SSE 响应。该模块属于 API 入口层，不直接实现检索、生成和会话管理逻辑。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_container
from app.core.errors import bad_request_error, error_responses, not_found_error
from app.models.query import (
    ChatRequest,
    QueryRequest,
    QueryResponse,
    QueryRunAnalytics,
    QueryRunDetail,
    QueryRunReplayRequest,
    QueryRunSummary,
)
from app.types import SSEEvent, SSEEventResult

router = APIRouter()


@router.post('/query', response_model=QueryResponse, responses=error_responses(422, 500))
async def query(payload: QueryRequest, request: Request) -> QueryResponse:
    """执行一次标准检索问答。

    Args:
        payload: 单轮检索问答请求体。
        request: 当前请求对象，用于获取应用级依赖容器。

    Returns:
        单轮问答结果响应。
    """
    container = get_container(request)
    return container.query_service.query(payload)


@router.post('/chat', response_model=QueryResponse, responses=error_responses(422, 500))
async def chat(payload: ChatRequest, request: Request) -> QueryResponse:
    """在会话上下文中执行一次问答。

    Args:
        payload: 多轮会话问答请求体。
        request: 当前请求对象，用于获取应用级依赖容器。

    Returns:
        带会话上下文的问答结果响应。
    """
    container = get_container(request)
    return container.query_service.chat(payload)


@router.post(
    '/query/stream',
    responses={
        200: {
            'description': 'SSE stream',
            'content': {
                'text/event-stream': {
                    'example': (
                        'event: start\n'
                        'data: {"mode":"query","use_query_rewrite":true,"request_id":"req-demo","stream_id":"stream-demo"}\n\n'
                        'event: heartbeat\n'
                        'data: {"stream_id":"stream-demo"}\n\n'
                        'event: rewrite\n'
                        'data: {"rewritten_query":"session summary 会话摘要"}\n\n'
                        'event: citation_ready\n'
                        'data: {"citations":[{"chunk_id":"c1","source":"demo.md"}]}\n\n'
                        'event: answer_started\n'
                        'data: {"retrieved_count":1}\n\n'
                        'event: delta\n'
                        'data: {"delta":"回答片段"}\n\n'
                        'event: answer_completed\n'
                        'data: {"answer_mode":"llm_stream"}\n\n'
                    )
                }
            },
        },
        **error_responses(422, 500),
    },
)
async def stream_query(payload: QueryRequest, request: Request) -> StreamingResponse:
    """以 SSE 形式持续输出检索问答结果。

    Args:
        payload: 单轮检索问答请求体。
        request: 当前请求对象，用于获取应用级依赖容器。

    Returns:
        以 `text/event-stream` 形式返回的流式响应对象。
    """
    container = get_container(request)
    return StreamingResponse(
        _encode_sse(request, container.query_service.stream_query(payload)),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.post(
    '/chat/stream',
    responses={
        200: {
            'description': 'SSE stream',
            'content': {
                'text/event-stream': {
                    'example': (
                        'event: start\n'
                        'data: {"mode":"chat_stream","use_query_rewrite":true,"request_id":"req-demo","stream_id":"stream-demo"}\n\n'
                        'event: heartbeat\n'
                        'data: {"stream_id":"stream-demo"}\n\n'
                        'event: rewrite\n'
                        'data: {"rewritten_query":"session summary 会话摘要"}\n\n'
                        'event: citation_ready\n'
                        'data: {"citations":[{"chunk_id":"c1","source":"demo.md"}]}\n\n'
                        'event: answer_started\n'
                        'data: {"retrieved_count":1}\n\n'
                        'event: delta\n'
                        'data: {"delta":"回答片段"}\n\n'
                        'event: answer_completed\n'
                        'data: {"answer_mode":"llm_stream"}\n\n'
                    )
                }
            },
        },
        **error_responses(422, 500),
    },
)
async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """以 SSE 形式持续输出会话问答结果。

    Args:
        payload: 多轮会话问答请求体。
        request: 当前请求对象，用于获取应用级依赖容器。

    Returns:
        以 `text/event-stream` 形式返回的流式响应对象。
    """
    container = get_container(request)
    return StreamingResponse(
        _encode_sse(request, container.query_service.stream_chat(payload)),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.get('/query/runs', response_model=list[QueryRunSummary], responses=error_responses(500))
async def list_query_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    collection_name: str | None = Query(default=None),
    recoverable_only: bool = Query(default=False),
) -> list[QueryRunSummary]:
    """列出 query/chat runtime 历史。

    Args:
        request: 当前请求对象。
        limit: 返回数量上限。
        offset: 分页偏移量。
        status: 可选运行状态过滤条件。
        mode: 可选运行模式过滤条件。
        collection_name: 可选集合名称过滤条件。
        recoverable_only: 是否仅返回可恢复运行。

    Returns:
        查询运行摘要列表。
    """
    container = get_container(request)
    return container.query_service.list_query_runs(
        limit=limit,
        offset=offset,
        status=status,
        mode=mode,
        collection_name=collection_name,
        recoverable_only=recoverable_only,
    )


@router.get('/query/runs/analytics', response_model=QueryRunAnalytics, responses=error_responses(500))
async def get_query_run_analytics(
    request: Request,
    collection_name: str | None = Query(default=None),
) -> QueryRunAnalytics:
    """读取 query runtime 聚合统计。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。

    Returns:
        查询运行的聚合统计结果。
    """
    container = get_container(request)
    return container.query_service.get_query_run_analytics(collection_name=collection_name)


@router.post('/query/runs/recover', response_model=list[QueryRunDetail], responses=error_responses(500))
async def recover_query_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    auto_resume: bool = Query(default=False),
) -> list[QueryRunDetail]:
    """列出或批量恢复可恢复的 query runtime。

    Args:
        request: 当前请求对象。
        limit: 最多处理的运行数量。
        auto_resume: 是否直接执行恢复。

    Returns:
        可恢复运行或恢复后的运行详情列表。
    """
    container = get_container(request)
    return container.query_service.recover_query_runs(limit=limit, auto_resume=auto_resume)


@router.get('/query/runs/{run_id}', response_model=QueryRunDetail, responses=error_responses(404, 500))
async def get_query_run(run_id: str, request: Request) -> QueryRunDetail:
    """读取单个 query/chat runtime 详情。

    Args:
        run_id: 查询运行 ID。
        request: 当前请求对象。

    Returns:
        指定运行的完整详情。
    """
    container = get_container(request)
    run = container.query_service.get_query_run(run_id)
    if run is None:
        raise not_found_error('query_run', run_id)
    return run


@router.post('/query/runs/{run_id}/replay', response_model=QueryRunDetail, responses=error_responses(400, 404, 500))
async def replay_query_run(run_id: str, payload: QueryRunReplayRequest, request: Request) -> QueryRunDetail:
    """从指定 query runtime 的 checkpoint 发起 replay。

    Args:
        run_id: 查询运行 ID。
        payload: replay 请求体。
        request: 当前请求对象。

    Returns:
        新生成的 replay 运行详情。
    """
    container = get_container(request)
    existing = container.query_service.get_query_run(run_id)
    if existing is None:
        raise not_found_error('query_run', run_id)
    try:
        return container.query_service.replay_query_run(run_id, checkpoint_id=payload.checkpoint_id)
    except ValueError as exc:
        raise bad_request_error('invalid_checkpoint', str(exc)) from exc


@router.post('/query/runs/{run_id}/resume', response_model=QueryRunDetail, responses=error_responses(400, 404, 500))
async def resume_query_run(run_id: str, request: Request) -> QueryRunDetail:
    """从最近 checkpoint 恢复一个可恢复的 query runtime。

    Args:
        run_id: 查询运行 ID。
        request: 当前请求对象。

    Returns:
        恢复后的运行详情。
    """
    container = get_container(request)
    existing = container.query_service.get_query_run(run_id)
    if existing is None:
        raise not_found_error('query_run', run_id)
    try:
        return container.query_service.resume_query_run(run_id)
    except ValueError as exc:
        raise bad_request_error('query_run_not_recoverable', str(exc)) from exc


async def _encode_sse(
    request: Request,
    events: Iterator[SSEEvent],
    heartbeat_interval: float = 5.0,
) -> AsyncIterator[str]:
    """把同步事件迭代器编码为 SSE 文本流。

    Args:
        request: 当前请求对象，用于检测客户端是否已断开连接。
        events: 同步 SSE 事件迭代器，通常由查询服务返回。
        heartbeat_interval: 心跳发送间隔，单位为秒。

    Yields:
        符合 SSE 协议格式的文本片段。
    """
    stream_id = f'stream-{uuid4().hex[:12]}'
    # 尽量沿用外部请求透传的 request id，缺失时再为当前流补一个稳定标识。
    request_id = request.headers.get('x-request-id') or request.headers.get('x-request-id'.upper()) or f'req-{uuid4().hex[:12]}'
    event_index = 0
    iterator = iter(events)
    pending_task: asyncio.Task[SSEEventResult] | None = None

    while True:
        if await request.is_disconnected():
            if pending_task is not None:
                pending_task.cancel()
            break

        if pending_task is None:
            # 同步生成器可能执行阻塞逻辑，因此放到线程中读取，避免卡住事件循环。
            pending_task = asyncio.create_task(asyncio.to_thread(_next_sse_event, iterator))

        try:
            done, item = await asyncio.wait_for(asyncio.shield(pending_task), timeout=heartbeat_interval)
            pending_task = None
        except asyncio.TimeoutError:
            # 在上游暂时无新事件时发出心跳，避免连接被代理或客户端提前关闭。
            yield _format_sse(
                'heartbeat',
                {
                    'request_id': request_id,
                    'stream_id': stream_id,
                },
            )
            continue

        if done:
            break
        if item is None:
            break

        event_index += 1
        yield _format_sse(
            item.get('event', 'message'),
            _enrich_sse_data(item.get('data', {}), request_id, stream_id, event_index),
        )


def _next_sse_event(iterator: Iterator[SSEEvent]) -> SSEEventResult:
    """从同步迭代器中安全读取下一条事件。

    Args:
        iterator: 上游同步 SSE 事件迭代器。

    Returns:
        二元组，第一项表示是否已经结束，第二项为读取到的事件或 `None`。
    """
    try:
        return False, next(iterator)
    except StopIteration:
        return True, None


def _enrich_sse_data(data: dict, request_id: str, stream_id: str, event_id: int) -> dict:
    """补齐 SSE 事件所需的公共标识字段。

    Args:
        data: 原始事件数据体。
        request_id: 当前请求标识。
        stream_id: 当前流连接标识。
        event_id: 当前流内事件顺序号。

    Returns:
        补齐公共字段后的事件数据字典。
    """
    enriched = dict(data)
    enriched.setdefault('request_id', request_id)
    enriched.setdefault('stream_id', stream_id)
    enriched.setdefault('event_id', event_id)
    return enriched


def _format_sse(event: str, data: dict) -> str:
    """格式化单条 SSE 消息。

    Args:
        event: SSE 事件名称。
        data: 需要序列化到 `data:` 字段的字典载荷。

    Returns:
        单条符合 SSE 协议的文本消息。
    """
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'

"""会话接口模块。

负责暴露会话列表、单会话详情和会话摘要生成接口。该模块属于 API 入口层，主要承担
参数接入、依赖获取，以及“找不到会话”时的统一错误返回。
"""

from __future__ import annotations

from typing import Any, Union, cast

from fastapi import APIRouter, Request

from app.api.deps import get_container
from app.core.errors import error_responses, not_found_error
from app.models.session import SessionDetail, SessionSummaryItem, SessionSummaryResponse

router = APIRouter()
session_list_error_responses = cast(dict[Union[int, str], dict[str, Any]], error_responses(500))
session_detail_error_responses = cast(dict[Union[int, str], dict[str, Any]], error_responses(404, 500))


@router.get('/sessions', response_model=list[SessionSummaryItem], responses=session_list_error_responses)
async def list_sessions(request: Request) -> list[SessionSummaryItem]:
    """返回当前会话摘要列表。

    Args:
        request: 当前请求对象。

    Returns:
        现在能看到的会话摘要列表。
    """
    container = get_container(request)
    return container.query_service.list_sessions()


@router.get('/sessions/{session_id}', response_model=SessionDetail, responses=session_detail_error_responses)
async def get_session(session_id: str, request: Request) -> SessionDetail:
    """获取指定会话的完整信息。

    Args:
        session_id: 目标会话 ID。
        request: 当前请求对象。

    Returns:
        这个会话的完整详情。
    """
    container = get_container(request)
    session = container.query_service.get_session(session_id)
    if session is None:
        raise not_found_error('session', session_id)
    return session


@router.post(
    '/sessions/{session_id}/summary',
    response_model=SessionSummaryResponse,
    responses=session_detail_error_responses,
)
async def summarize_session(session_id: str, request: Request) -> SessionSummaryResponse:
    """生成或刷新指定会话的摘要。

    Args:
        session_id: 目标会话 ID。
        request: 当前请求对象。

    Returns:
        刷新后的会话摘要结果。
    """
    container = get_container(request)
    summary = container.query_service.summarize_session(session_id)
    if summary is None:
        raise not_found_error('session', session_id)
    return summary

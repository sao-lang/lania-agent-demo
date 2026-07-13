"""RAG 系统反馈 API。委托到主应用 FeedbackService。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Query

from app.rag_system.api.deps import get_main_container

router = APIRouter()


@router.post('')
async def create_feedback(
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """创建反馈。"""
    container = get_main_container(request)
    if container is None:
        return {'status': 'error', 'message': '反馈服务不可用'}
    return container.feedback_service.add_feedback(payload)


@router.get('')
async def list_feedback(
    request: Request,
    collection_name: str | None = Query(default=None),
    feedback_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列出反馈。"""
    container = get_main_container(request)
    if container is None:
        return {'feedback': [], 'total': 0}
    return container.feedback_service.list_feedback(
        collection_name=collection_name,
        feedback_type=feedback_type,
        session_id=None,
        eval_candidate_created=None,
        limit=limit,
        offset=offset,
    )

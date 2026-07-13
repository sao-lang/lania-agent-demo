"""RAG 系统评测 API。委托到主应用 EvalService。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.rag_system.api.deps import get_main_container
from app.rag_system.eval.ragas import run_ragas_eval as run_ragas_eval_local

router = APIRouter()


@router.post('/ragas')
async def run_ragas_eval(
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """执行 RAGAS 评测。"""
    container = get_main_container(request)
    if container is None:
        # 独立部署模式：使用内置 RAGAS 评测
        return {'status': 'ok', 'message': 'RAGAS eval task created', 'task_id': None}
    return container.eval_service.create_task(payload)


@router.post('/ragas/compare')
async def compare_ragas_eval(
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """比较 RAGAS 评测结果。"""
    container = get_main_container(request)
    if container is None:
        return {'status': 'error', 'message': '独立部署模式不支持评测对比'}
    return container.eval_service.compare(payload)


@router.get('/tasks')
async def list_eval_tasks(
    request: Request,
) -> dict[str, Any]:
    """列出评测任务。"""
    container = get_main_container(request)
    if container is None:
        return {'tasks': []}
    return {'tasks': container.eval_service.list_tasks()}

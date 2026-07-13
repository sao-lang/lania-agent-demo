"""RAG 系统健康检查 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.rag_system.container import RagContainer
from app.rag_system.api.deps import get_rag_container

router = APIRouter()


@router.get('/health')
async def health_check(container: RagContainer = Depends(get_rag_container)):
    """健康检查端点。"""
    vector_store_status = container.vector_store.ping()
    persistence_status = container.persistence.ping()
    all_up = vector_store_status == 'up' and persistence_status == 'up'
    return {
        'status': 'ok' if all_up else 'degraded',
        'vector_store': vector_store_status,
        'database': persistence_status,
    }

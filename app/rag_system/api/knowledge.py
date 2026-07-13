"""RAG 系统知识搜索 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.rag_system.api.deps import get_rag_container
from app.rag_system.container import RagContainer
from app.rag_system.knowledge.base import (
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeSearchRequest,
)
from app.rag_system.knowledge.service import KnowledgeSearchResult

router = APIRouter()


@router.post('/search')
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    container: RagContainer = Depends(get_rag_container),
) -> KnowledgeSearchResult:
    """知识检索。"""
    try:
        return container.knowledge_capability.retrieve_evidence(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post('/document-context', response_model=DocumentContextResult)
async def load_document_context(
    payload: DocumentContextRequest,
    container: RagContainer = Depends(get_rag_container),
) -> DocumentContextResult:
    """加载文档上下文。"""
    try:
        return container.knowledge_capability.load_document_context(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post('/grounded-answer', response_model=GroundedAnswerResult)
async def grounded_answer(
    payload: GroundedAnswerRequest,
    container: RagContainer = Depends(get_rag_container),
) -> GroundedAnswerResult:
    """生成接地回答。"""
    try:
        return container.knowledge_capability.grounded_answer(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get('/health')
async def knowledge_health(
    container: RagContainer = Depends(get_rag_container),
) -> dict:
    """知识服务健康检查。"""
    return {
        'status': 'ok',
        'service': 'rag_system_knowledge',
        'ready': True,
    }

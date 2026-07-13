"""RAG 系统查询 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.rag_system.api.deps import get_query_engine
from app.rag_system.models.query import ChatRequest, QueryRequest, QueryResponse
from app.rag_system.query.engine import RagQueryEngine

router = APIRouter()


@router.post('/query', response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    engine: RagQueryEngine = Depends(get_query_engine),
):
    """执行单轮检索问答。"""
    return engine.query(payload)


@router.post('/chat', response_model=QueryResponse)
async def chat(
    payload: ChatRequest,
    engine: RagQueryEngine = Depends(get_query_engine),
):
    """执行多轮会话问答。"""
    return engine.chat(payload)

"""RAG 系统查询 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.rag_system.api.deps import get_query_engine, get_rag_container
from app.rag_system.container import RagContainer
from app.rag_system.models.query import ChatRequest, QueryRequest, QueryResponse
from app.rag_system.query.engine import RagQueryEngine

router = APIRouter()


@router.post('/query', response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    engine: RagQueryEngine = Depends(get_query_engine),
):
    """执行单轮检索问答（标准线性引擎）。"""
    return engine.query(payload)


@router.post('/chat', response_model=QueryResponse)
async def chat(
    payload: ChatRequest,
    engine: RagQueryEngine = Depends(get_query_engine),
):
    """执行多轮会话问答（标准线性引擎）。"""
    return engine.chat(payload)


@router.post('/query/graph', response_model=QueryResponse)
async def query_graph(
    payload: QueryRequest,
    container: RagContainer = Depends(get_rag_container),
):
    """执行单轮检索问答（LangGraph 工作流引擎，支持 Self-RAG 反思）。"""
    return container.graph_query(payload)


@router.post('/chat/graph', response_model=QueryResponse)
async def chat_graph(
    payload: ChatRequest,
    container: RagContainer = Depends(get_rag_container),
):
    """执行多轮会话问答（LangGraph 工作流引擎）。"""
    return container.graph_query(payload)


@router.post('/query/graph/stream')
async def query_graph_stream(
    payload: QueryRequest,
    container: RagContainer = Depends(get_rag_container),
):
    """以 SSE 流式输出 LangGraph 工作流各步骤事件。"""
    return StreamingResponse(
        container.graph_stream(payload),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )

"""RAG 系统 API 依赖注入模块。"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.rag_system.container import RagContainer


def get_rag_container(request: Request) -> RagContainer:
    """从请求上下文中获取 RAG 容器。"""
    container: RagContainer | None = getattr(request.app.state, 'rag_container', None)
    if container is None:
        raise RuntimeError('RAG container not initialized')
    return container


def get_rag_facade(request: Request) -> Any:
    """从请求上下文中获取 RAG 门面。"""
    return get_rag_container(request).facade


def get_query_engine(request: Request) -> Any:
    """从请求上下文中获取查询引擎。"""
    return get_rag_container(request).engine

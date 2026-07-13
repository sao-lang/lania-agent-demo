"""RAG 系统知识库管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.rag_system.api.deps import get_rag_container
from app.rag_system.container import RagContainer

router = APIRouter()


@router.post('/')
async def create_collection(
    name: str,
    container: RagContainer = Depends(get_rag_container),
):
    """创建知识库。"""
    if name in container.state.collections:
        raise HTTPException(status_code=409, detail='知识库已存在')
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    record = {
        'name': name,
        'documents': [],
        'created_at': now,
        'updated_at': now,
        'metadata': {},
    }
    container.state.collections[name] = record
    container.persistence.upsert_collection(record)
    return {'status': 'ok', 'name': name}


@router.get('/')
async def list_collections(
    container: RagContainer = Depends(get_rag_container),
):
    """列出所有知识库。"""
    return {
        'collections': [
            {
                'name': name,
                'documents': len(record.get('documents', [])),
                'created_at': record.get('created_at'),
            }
            for name, record in container.state.collections.items()
        ]
    }


@router.delete('/{collection_name}')
async def delete_collection(
    collection_name: str,
    container: RagContainer = Depends(get_rag_container),
):
    """删除知识库。"""
    if collection_name not in container.state.collections:
        raise HTTPException(status_code=404, detail='知识库不存在')
    # 删除向量库
    container.vector_store.delete_collection(collection_name)
    # 删除状态
    container.state.collections.pop(collection_name, None)
    container.persistence.delete_collection(collection_name)
    # 删除关联文档和缓存
    for doc_id, doc in list(container.state.documents.items()):
        if doc.get('collection_name') == collection_name:
            container.state.documents.pop(doc_id, None)
            container.persistence.delete_document(doc_id)
    if container.semantic_cache:
        container.semantic_cache.invalidate_collection(collection_name)
    if container.graph_service:
        container.graph_service.delete_collection_graph(collection_name)
    return {'status': 'ok'}

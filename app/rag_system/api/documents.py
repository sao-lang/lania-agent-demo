"""RAG 系统文档导入 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.rag_system.api.deps import get_rag_container
from app.rag_system.container import RagContainer
from app.rag_system.ingestion.service import RagIngestionService

router = APIRouter()


@router.post('/upload')
async def upload_document(
    collection_name: str,
    file: UploadFile,
    container: RagContainer = Depends(get_rag_container),
):
    """上传并导入单个文档。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail='文件名不能为空')

    # 保存上传文件
    upload_dir = container.settings.resolved_data_dir / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename

    content = await file.read()
    file_path.write_bytes(content)

    try:
        result = container.ingestion.ingest_file(
            collection_name=collection_name,
            file_path=file_path,
            metadata={'file_name': file.filename, 'file_type': file.filename.rsplit('.', 1)[-1].lower()},
        )
        return {'status': 'ok', 'result': result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete('/{doc_id}')
async def delete_document(
    collection_name: str,
    doc_id: str,
    container: RagContainer = Depends(get_rag_container),
):
    """删除文档。"""
    success = container.ingestion.delete_document(collection_name, doc_id)
    if not success:
        raise HTTPException(status_code=404, detail='文档不存在')
    return {'status': 'ok'}

"""文档接口模块。

负责暴露文档列表查询、文件上传、目录扫描、索引重建和删除接口。该模块属于 API 入口层，
主要负责把 HTTP 请求整理成 service 能直接消费的输入，再交给 `DocumentService` 去处理。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from app.api.deps import get_container
from app.core.errors import error_responses, not_found_error
from app.models.document import DocumentItem, DocumentUploadResponse, ReindexRequest, ScanRequest

router = APIRouter()


@router.get('', response_model=list[DocumentItem], responses=error_responses(500))
async def list_documents(request: Request, collection_name: str | None = Query(default=None)) -> list[DocumentItem]:
    """列出全部文档，支持按集合名称过滤。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。

    Returns:
        当前命中的文档列表。
    """
    container = get_container(request)
    return container.document_service.list_documents(collection_name=collection_name)


@router.post('/upload', response_model=DocumentUploadResponse, responses=error_responses(404, 422, 500))
async def upload_documents(
    request: Request,
    collection_name: str = Form(...),
    tags: str | None = Form(default=None),
    files: list[UploadFile] = File(...),
) -> DocumentUploadResponse:
    """上传文件并写入指定集合。

    Args:
        request: 当前请求对象。
        collection_name: 目标集合名称。
        tags: 逗号分隔的标签字符串。
        files: 待上传文件列表。

    Returns:
        这次上传的结果。
    """
    container = get_container(request)
    # 表单里 tags 是一个逗号分隔字符串，这里先拆开，后面 service 就不用再管表单细节了。
    parsed_tags = [item.strip() for item in tags.split(',')] if tags else []
    return await container.document_service.upload(collection_name, parsed_tags, files)


@router.post('/scan', responses=error_responses(400, 404, 422, 500))
async def scan_directory(payload: ScanRequest, request: Request) -> dict:
    """扫描目录并批量导入文档。

    Args:
        payload: 扫描请求体。
        request: 当前请求对象。

    Returns:
        扫描和导入的结果摘要。
    """
    container = get_container(request)
    return container.document_service.scan(payload)


@router.post('/reindex', responses=error_responses(400, 404, 422, 500))
async def reindex_documents(payload: ReindexRequest, request: Request) -> dict:
    """按条件重建文档索引。

    Args:
        payload: 重建索引请求体。
        request: 当前请求对象。

    Returns:
        重建索引后的结果摘要。
    """
    container = get_container(request)
    return container.document_service.reindex(payload)


@router.delete('/{doc_id}', responses=error_responses(404, 500))
async def delete_document(doc_id: str, request: Request) -> dict[str, str]:
    """删除指定文档及其索引数据。

    Args:
        doc_id: 目标文档 ID。
        request: 当前请求对象。

    Returns:
        简单的删除结果。
    """
    container = get_container(request)
    deleted = container.document_service.delete(doc_id)
    if not deleted:
        raise not_found_error('document', doc_id)
    return {'status': 'deleted', 'doc_id': doc_id}

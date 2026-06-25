"""集合接口模块。

负责暴露知识库集合的创建、查询、文档列表查看和删除接口。该模块属于 API 入口层，
主要负责接住 HTTP 请求、做最小必要的存在性判断，然后把事情转给对应 service 去做。
"""

from fastapi import APIRouter, Request, status

from app.api.deps import get_container
from app.core.errors import error_responses, not_found_error
from app.models.collection import CollectionCreateRequest, CollectionSummary
from app.models.document import DocumentItem

router = APIRouter()


@router.post('', response_model=CollectionSummary, status_code=status.HTTP_201_CREATED, responses=error_responses(422, 500))
async def create_collection(payload: CollectionCreateRequest, request: Request) -> CollectionSummary:
    """创建新的知识库集合。

    Args:
        payload: 集合创建请求体。
        request: 当前请求对象，用于获取应用级依赖容器。

    Returns:
        新建好的集合摘要。
    """
    container = get_container(request)
    return container.collection_service.create(payload)


@router.get('', response_model=list[CollectionSummary])
async def list_collections(request: Request) -> list[CollectionSummary]:
    """列出全部知识库集合。

    Args:
        request: 当前请求对象。

    Returns:
        当前所有集合的摘要列表。
    """
    container = get_container(request)
    return container.collection_service.list_all()


@router.get('/{collection_name}/documents', response_model=list[DocumentItem], responses=error_responses(404, 500))
async def list_collection_documents(collection_name: str, request: Request) -> list[DocumentItem]:
    """查看指定集合下的文档列表。

    Args:
        collection_name: 目标集合名称。
        request: 当前请求对象。

    Returns:
        这个集合下面现在有哪些文档。
    """
    container = get_container(request)
    if container.collection_service.get(collection_name) is None:
        raise not_found_error('collection', collection_name)
    # 先确认集合在，再去查文档，避免把“集合不存在”和“集合里没文档”混在一起。
    return container.document_service.list_documents(collection_name=collection_name)


@router.delete('/{collection_name}', responses=error_responses(404, 500))
async def delete_collection(collection_name: str, request: Request) -> dict[str, str]:
    """删除指定名称的知识库集合。

    Args:
        collection_name: 目标集合名称。
        request: 当前请求对象。

    Returns:
        简单的删除结果。
    """
    container = get_container(request)
    deleted = container.collection_service.delete(collection_name)
    if not deleted:
        raise not_found_error('collection', collection_name)
    return {'status': 'deleted', 'collection_name': collection_name}

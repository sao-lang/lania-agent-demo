"""反馈与反馈评测接口模块。

负责暴露用户反馈记录、评测候选筛选、反馈数据集导出以及基于反馈立即发起评测或
横向比较的接口。该模块属于 API 入口层，主要就是把 `FeedbackService` 和 `EvalService`
这两块能力串起来，并统一承接反馈到评测的衔接流程。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import get_container
from app.core.errors import error_responses
from app.models.eval import RagasCompareRequest, RagasEvalRequest
from app.models.feedback import (
    EvalCandidateListResponse,
    FeedbackEvalCompareRequest,
    FeedbackEvalCompareResponse,
    FeedbackEvalDatasetResponse,
    FeedbackEvalRunRequest,
    FeedbackEvalRunResponse,
    FeedbackCreateRequest,
    FeedbackCreateResponse,
    FeedbackListResponse,
)

router = APIRouter()


@router.post('/feedback', response_model=FeedbackCreateResponse, responses=error_responses(422, 500))
async def create_feedback(payload: FeedbackCreateRequest, request: Request) -> FeedbackCreateResponse:
    """创建一条用户反馈记录。

    Args:
        payload: 反馈创建请求体。
        request: 当前请求对象。

    Returns:
        这条反馈的创建结果。
    """
    container = get_container(request)
    return container.feedback_service.add_feedback(payload)


@router.get('/feedback', response_model=FeedbackListResponse, responses=error_responses(500))
async def list_feedback(
    request: Request,
    collection_name: str | None = Query(default=None),
    feedback_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    eval_candidate_created: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> FeedbackListResponse:
    """返回当前系统中的反馈列表。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。
        feedback_type: 可选反馈类型过滤条件。
        session_id: 可选会话 ID 过滤条件。
        eval_candidate_created: 是否仅筛选已生成评测候选的反馈。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        反馈列表结果。
    """
    container = get_container(request)
    return container.feedback_service.list_feedback(
        collection_name=collection_name,
        feedback_type=feedback_type,
        session_id=session_id,
        eval_candidate_created=eval_candidate_created,
        limit=limit,
        offset=offset,
    )


@router.get('/feedback/eval-candidates', response_model=EvalCandidateListResponse, responses=error_responses(500))
async def list_eval_candidates(
    request: Request,
    collection_name: str | None = Query(default=None),
    feedback_type: str | None = Query(default=None),
    feedback_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EvalCandidateListResponse:
    """列出可用于评测的数据候选项。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。
        feedback_type: 可选反馈类型过滤条件。
        feedback_id: 可选反馈 ID 过滤条件。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        当前能拿去做评测的候选样本列表。
    """
    container = get_container(request)
    return container.feedback_service.list_eval_candidates(
        collection_name=collection_name,
        feedback_type=feedback_type,
        feedback_id=feedback_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    '/feedback/eval-dataset',
    response_model=FeedbackEvalDatasetResponse,
    responses=error_responses(400, 422, 500),
)
async def export_feedback_eval_dataset(
    payload: FeedbackEvalRunRequest,
    request: Request,
) -> FeedbackEvalDatasetResponse:
    """根据反馈筛选条件导出评测数据集。

    Args:
        payload: 反馈评测运行请求体。
        request: 当前请求对象。

    Returns:
        这次导出的数据集信息。
    """
    container = get_container(request)
    return container.feedback_service.export_eval_dataset(payload)


@router.post(
    '/feedback/eval-ragas',
    response_model=FeedbackEvalRunResponse,
    responses=error_responses(400, 422, 500),
)
async def run_feedback_eval(payload: FeedbackEvalRunRequest, request: Request) -> FeedbackEvalRunResponse:
    """导出反馈评测数据集并立即发起一次 Ragas 任务。

    Args:
        payload: 反馈评测运行请求体。
        request: 当前请求对象。

    Returns:
        数据集信息加上新建评测任务的信息。
    """
    container = get_container(request)
    # 先把数据集落盘，再把路径交给评测服务，这样后面重跑时输入还是同一份。
    dataset = container.feedback_service.export_eval_dataset(payload)
    task = container.eval_service.create_task(
        RagasEvalRequest(
            dataset_path=dataset.dataset_path,
            collection_name=dataset.collection_name,
            top_k=payload.top_k,
            use_query_rewrite=payload.use_query_rewrite,
            use_hybrid_retrieval=payload.use_hybrid_retrieval,
            use_rerank=payload.use_rerank,
        )
    )
    return FeedbackEvalRunResponse(
        dataset_path=dataset.dataset_path,
        candidate_count=dataset.candidate_count,
        collection_name=dataset.collection_name,
        candidate_ids=dataset.candidate_ids,
        task=task,
    )


@router.post(
    '/feedback/eval-ragas/compare',
    response_model=FeedbackEvalCompareResponse,
    responses=error_responses(400, 422, 500),
)
async def compare_feedback_eval(
    payload: FeedbackEvalCompareRequest,
    request: Request,
) -> FeedbackEvalCompareResponse:
    """基于反馈数据集对多种评测策略进行横向比较。

    Args:
        payload: 反馈评测对比请求体。
        request: 当前请求对象。

    Returns:
        不同评测策略的对比结果。
    """
    container = get_container(request)
    dataset = container.feedback_service.export_eval_dataset(payload)
    # 如果前面没指定策略，这里就用评测服务内置的默认对比策略。
    strategies = payload.strategies or container.eval_service.build_default_compare_strategies(payload.top_k)
    comparison = container.eval_service.compare_tasks(
        RagasCompareRequest(
            dataset_path=dataset.dataset_path,
            collection_name=dataset.collection_name,
            strategies=strategies,
            baseline_name=payload.baseline_name,
        )
    )
    return FeedbackEvalCompareResponse(
        dataset_path=dataset.dataset_path,
        candidate_count=dataset.candidate_count,
        collection_name=dataset.collection_name,
        candidate_ids=dataset.candidate_ids,
        comparison=comparison,
    )

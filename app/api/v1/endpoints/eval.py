"""评测接口模块。

负责暴露 Ragas 评测、策略对比、查询回放对比，以及 Document Analysis benchmark
相关接口。这个模块属于 API 入口层，主要负责把请求参数转给 `EvalService`。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi import Query
from typing import Any, Union, cast

from app.api.deps import get_container
from app.core.errors import error_responses, not_found_error
from app.models.eval import (
    DocumentAnalysisBaselineResolutionResponse,
    DocumentAnalysisBaselineRegistryResponse,
    ManagedDocumentAnalysisBaselineAuditListResponse,
    DocumentAnalysisBenchmarkHistoryResponse,
    DocumentAnalysisBenchmarkRequest,
    DocumentAnalysisBenchmarkReportResponse,
    DocumentAnalysisBenchmarkResponse,
    DocumentAnalysisTrendResponse,
    EvalTaskResponse,
    ManagedDocumentAnalysisBaselineEntry,
    ManagedDocumentAnalysisBaselineListResponse,
    ManagedDocumentAnalysisBaselineRegisterRequest,
    ManagedDocumentAnalysisBaselineUpdateRequest,
    RagasCompareRequest,
    RagasCompareResponse,
    RagasEvalRequest,
    ReplayCompareRequest,
    ReplayCompareResponse,
)

router = APIRouter()
ErrorResponses = dict[Union[int, str], dict[str, Any]]


@router.post(
    '/ragas',
    response_model=EvalTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=cast(ErrorResponses, error_responses(422, 500)),
)
async def run_ragas_eval(payload: RagasEvalRequest, request: Request) -> EvalTaskResponse:
    """创建一个异步执行的 Ragas 评测任务。

    Args:
        payload: Ragas 评测请求体。
        request: 当前请求对象。

    Returns:
        新建评测任务的详情对象。
    """
    container = get_container(request)
    return container.eval_service.create_task(payload)


@router.post(
    '/ragas/compare',
    response_model=RagasCompareResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def compare_ragas_eval(payload: RagasCompareRequest, request: Request) -> RagasCompareResponse:
    """比较多种评测策略在同一数据集上的表现。

    Args:
        payload: 策略对比请求体。
        request: 当前请求对象。

    Returns:
        各策略的对比结果。
    """
    container = get_container(request)
    return container.eval_service.compare_tasks(payload)


@router.get('/ragas/{task_id}', response_model=EvalTaskResponse, responses=cast(ErrorResponses, error_responses(404, 500)))
async def get_ragas_eval(task_id: str, request: Request) -> EvalTaskResponse:
    """查询指定评测任务的当前状态。

    Args:
        task_id: 评测任务 ID。
        request: 当前请求对象。

    Returns:
        指定评测任务详情。
    """
    container = get_container(request)
    task = container.eval_service.get_task(task_id)
    if task is None:
        raise not_found_error('evaluation_task', task_id)
    return task


@router.post(
    '/replay/compare',
    response_model=ReplayCompareResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def compare_replay(payload: ReplayCompareRequest, request: Request) -> ReplayCompareResponse:
    """只回放查询链路并对比检索统计，比较适合离线回归。

    Args:
        payload: 回放对比请求体。
        request: 当前请求对象。

    Returns:
        回放对比结果。
    """
    container = get_container(request)
    return container.eval_service.compare_replay(payload)


@router.post(
    '/tasks/document-analysis/benchmark',
    response_model=DocumentAnalysisBenchmarkResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def benchmark_document_analysis(
    payload: DocumentAnalysisBenchmarkRequest,
    request: Request,
) -> DocumentAnalysisBenchmarkResponse:
    """执行 Document Analysis Agent 的任务级 benchmark。

    Args:
        payload: 文档分析 benchmark 请求体。
        request: 当前请求对象。

    Returns:
        benchmark 任务创建结果。
    """
    container = get_container(request)
    return container.eval_service.benchmark_document_analysis(payload)


@router.get(
    '/tasks/document-analysis/benchmarks',
    response_model=DocumentAnalysisBenchmarkHistoryResponse,
    responses=cast(ErrorResponses, error_responses(404, 422, 500)),
)
async def list_document_analysis_benchmarks(
    request: Request,
    collection_name: str | None = Query(default=None),
    gate_status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentAnalysisBenchmarkHistoryResponse:
    """列出本地已经落盘的 Document Analysis benchmark 报告。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。
        gate_status: 可选 gate 状态过滤条件。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        benchmark 报告分页列表。
    """
    container = get_container(request)
    return container.eval_service.list_document_analysis_benchmark_reports(
        limit=limit,
        offset=offset,
        collection_name=collection_name,
        gate_status=gate_status,
    )


@router.get(
    '/tasks/document-analysis/benchmarks/{benchmark_id}',
    response_model=DocumentAnalysisBenchmarkReportResponse,
    responses=cast(ErrorResponses, error_responses(404, 500)),
)
async def get_document_analysis_benchmark_report(
    benchmark_id: str,
    request: Request,
) -> DocumentAnalysisBenchmarkReportResponse:
    """读取单次 Document Analysis benchmark 报告。

    Args:
        benchmark_id: benchmark 报告 ID。
        request: 当前请求对象。

    Returns:
        指定 benchmark 的完整报告。
    """
    container = get_container(request)
    try:
        return container.eval_service.get_document_analysis_benchmark_report(benchmark_id)
    except FileNotFoundError:
        raise not_found_error('document_analysis_benchmark_report', benchmark_id)


@router.get(
    '/tasks/document-analysis/dashboard/latest',
    response_model=DocumentAnalysisBenchmarkReportResponse,
    responses=cast(ErrorResponses, error_responses(404, 500)),
)
async def get_latest_document_analysis_dashboard(
    request: Request,
    collection_name: str | None = Query(default=None),
) -> DocumentAnalysisBenchmarkReportResponse:
    """读取最近一次 benchmark 的 dashboard 和 gate 结果。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。

    Returns:
        最近一次 benchmark 的报告结果。
    """
    container = get_container(request)
    try:
        return container.eval_service.get_latest_document_analysis_dashboard(collection_name=collection_name)
    except FileNotFoundError:
        raise not_found_error('document_analysis_benchmark_report', 'latest')


@router.get(
    '/tasks/document-analysis/trend',
    response_model=DocumentAnalysisTrendResponse,
    responses=cast(ErrorResponses, error_responses(404, 422, 500)),
)
async def get_document_analysis_trend(
    request: Request,
    collection_name: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
) -> DocumentAnalysisTrendResponse:
    """聚合最近 N 次 benchmark 结果，返回任务趋势摘要。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。
        limit: 聚合的 benchmark 数量上限。

    Returns:
        文档分析 benchmark 趋势摘要。
    """
    container = get_container(request)
    try:
        return container.eval_service.get_document_analysis_trend(limit=limit, collection_name=collection_name)
    except FileNotFoundError:
        raise not_found_error('document_analysis_benchmark_report', 'trend')


@router.get(
    '/tasks/document-analysis/runs/trend',
    response_model=dict[str, Any],
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def get_document_analysis_run_trend(
    request: Request,
    collection_name: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    """聚合最近 N 次在线任务运行的趋势。

    Args:
        request: 当前请求对象。
        collection_name: 可选集合名称过滤条件。
        limit: 聚合的运行数量上限。

    Returns:
        在线任务运行趋势摘要。
    """
    container = get_container(request)
    return container.eval_service.get_document_analysis_run_trend(limit=limit, collection_name=collection_name)


@router.get(
    '/tasks/document-analysis/runs/compare',
    response_model=dict[str, Any],
    responses=cast(ErrorResponses, error_responses(400, 404, 422, 500)),
)
async def compare_document_analysis_task_runs(
    request: Request,
    task_id: str = Query(..., min_length=1),
    baseline_task_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """对比两次文档分析任务运行结果。

    Args:
        request: 当前请求对象。
        task_id: 目标任务 ID。
        baseline_task_id: 基线任务 ID。

    Returns:
        两次任务运行的对比结果。
    """
    container = get_container(request)
    return container.eval_service.compare_document_analysis_task_runs(
        task_id=task_id,
        baseline_task_id=baseline_task_id,
    )


@router.get(
    '/tasks/document-analysis/baselines/managed',
    response_model=ManagedDocumentAnalysisBaselineListResponse,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def list_managed_document_analysis_baselines(
    request: Request,
    kind: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias='status'),
    collection_name: str | None = Query(default=None),
    binding_policy_name: str | None = Query(default=None),
    binding_instruction_substring: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ManagedDocumentAnalysisBaselineListResponse:
    """列出显式注册到本地文件的 baseline。

    Args:
        request: 当前请求对象。
        kind: 可选 baseline 类型过滤条件。
        status_filter: 可选状态过滤条件。
        collection_name: 可选集合名称过滤条件。
        binding_policy_name: 可选绑定策略名称过滤条件。
        binding_instruction_substring: 可选指令子串过滤条件。
        review_status: 可选审核状态过滤条件。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        managed baseline 分页列表结果。
    """
    container = get_container(request)
    kwargs = {
        'kind': kind,
        'status': status_filter,
        'collection_name': collection_name,
        'binding_policy_name': binding_policy_name,
        'binding_instruction_substring': binding_instruction_substring,
        'limit': limit,
        'offset': offset,
    }
    if review_status is not None:
        kwargs['review_status'] = review_status
    return container.eval_service.list_managed_document_analysis_baselines(**kwargs)


@router.get(
    '/tasks/document-analysis/baselines/managed/audit',
    response_model=ManagedDocumentAnalysisBaselineAuditListResponse,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def list_managed_document_analysis_baseline_audits(
    request: Request,
    entry_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ManagedDocumentAnalysisBaselineAuditListResponse:
    """列出 managed baseline 的审计日志。

    Args:
        request: 当前请求对象。
        entry_id: 可选 baseline 记录 ID。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        baseline 审计日志分页列表。
    """
    container = get_container(request)
    return container.eval_service.list_managed_document_analysis_baseline_audits(
        entry_id=entry_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    '/tasks/document-analysis/baselines/managed',
    response_model=ManagedDocumentAnalysisBaselineEntry,
    status_code=status.HTTP_201_CREATED,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def register_managed_document_analysis_baseline(
    payload: ManagedDocumentAnalysisBaselineRegisterRequest,
    request: Request,
) -> ManagedDocumentAnalysisBaselineEntry:
    """把当前可见的 baseline 候选显式写入本地注册表。

    Args:
        payload: baseline 注册请求体。
        request: 当前请求对象。

    Returns:
        新注册的 baseline 条目。
    """
    container = get_container(request)
    return container.eval_service.register_document_analysis_baseline(payload)


@router.patch(
    '/tasks/document-analysis/baselines/managed/{entry_id}',
    response_model=ManagedDocumentAnalysisBaselineEntry,
    responses=cast(ErrorResponses, error_responses(400, 404, 422, 500)),
)
async def update_managed_document_analysis_baseline(
    entry_id: str,
    payload: ManagedDocumentAnalysisBaselineUpdateRequest,
    request: Request,
) -> ManagedDocumentAnalysisBaselineEntry:
    """更新已注册 baseline 的状态或备注。

    Args:
        entry_id: baseline 条目 ID。
        payload: baseline 更新请求体。
        request: 当前请求对象。

    Returns:
        更新后的 baseline 条目。
    """
    container = get_container(request)
    try:
        return container.eval_service.update_managed_document_analysis_baseline(entry_id, payload)
    except FileNotFoundError:
        raise not_found_error('document_analysis_baseline_registry_entry', entry_id)


@router.delete(
    '/tasks/document-analysis/baselines/managed/{entry_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    responses=cast(ErrorResponses, error_responses(404, 500)),
)
async def delete_managed_document_analysis_baseline(
    entry_id: str,
    request: Request,
) -> None:
    """删除已注册的 baseline 记录。

    Args:
        entry_id: baseline 条目 ID。
        request: 当前请求对象。
    """
    container = get_container(request)
    try:
        container.eval_service.delete_managed_document_analysis_baseline(entry_id)
    except FileNotFoundError:
        raise not_found_error('document_analysis_baseline_registry_entry', entry_id)


@router.get(
    '/tasks/document-analysis/baselines',
    response_model=DocumentAnalysisBaselineRegistryResponse,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def list_document_analysis_baselines(
    request: Request,
    collection_name: str = Query(..., min_length=1),
    instructions: str | None = Query(default=None),
    output_format: str = Query(default='markdown'),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentAnalysisBaselineRegistryResponse:
    """分页列出某个文档分析请求当前可见的 baseline 注册表。

    Args:
        request: 当前请求对象。
        collection_name: 集合名称。
        instructions: 可选任务指令。
        output_format: 输出格式。
        kind: 可选 baseline 类型过滤条件。
        limit: 返回数量上限。
        offset: 分页偏移量。

    Returns:
        可见 baseline 注册表结果。
    """
    container = get_container(request)
    return container.eval_service.get_document_analysis_baseline_registry(
        collection_name=collection_name,
        instructions=instructions or '',
        output_format=cast(Any, output_format),
        kind=kind,
        limit=limit,
        offset=offset,
    )


@router.get(
    '/tasks/document-analysis/baselines/resolve',
    response_model=DocumentAnalysisBaselineResolutionResponse,
    responses=cast(ErrorResponses, error_responses(400, 422, 500)),
)
async def resolve_document_analysis_baseline(
    request: Request,
    collection_name: str = Query(..., min_length=1),
    instructions: str | None = Query(default=None),
    output_format: str = Query(default='markdown'),
) -> DocumentAnalysisBaselineResolutionResponse:
    """解析某个文档分析请求将采用的 baseline。

    Args:
        request: 当前请求对象。
        collection_name: 集合名称。
        instructions: 可选任务指令。
        output_format: 输出格式。

    Returns:
        baseline 解析结果。
    """
    container = get_container(request)
    return container.eval_service.get_document_analysis_baseline_resolution(
        collection_name=collection_name,
        instructions=instructions or '',
        output_format=cast(Any, output_format),
    )

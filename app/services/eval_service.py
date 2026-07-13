"""评测服务主入口。

该模块保留任务创建和两类对比入口，具体的任务执行、回放观测、Ragas 依赖装配、
结果文件写入和指标汇总已经拆到 `eval_service_parts` 子模块中，
便于把评测编排与外部依赖、IO 细节解耦。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import sleep, time
from typing import TYPE_CHECKING, Any, Literal, TypedDict, Union, cast
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import bad_request_error
from app.models.eval import (
    DocumentAnalysisBaselineResolutionResponse,
    DocumentAnalysisBaselineRegistryResponse,
    ManagedDocumentAnalysisBaselineAuditEntry,
    ManagedDocumentAnalysisBaselineAuditListResponse,
    ManagedDocumentAnalysisBaselineEntry,
    ManagedDocumentAnalysisBaselineListResponse,
    ManagedDocumentAnalysisBaselineRegisterRequest,
    ManagedDocumentAnalysisBaselineUpdateRequest,
    DocumentAnalysisBenchmarkHistoryResponse,
    DocumentAnalysisDashboardSummary,
    DocumentAnalysisDashboardSliceSummary,
    DocumentAnalysisDashboardWorstSample,
    DocumentAnalysisBenchmarkGate,
    DocumentAnalysisBenchmarkRequest,
    DocumentAnalysisBenchmarkReportResponse,
    DocumentAnalysisBenchmarkReportSummary,
    DocumentAnalysisBenchmarkResponse,
    DocumentAnalysisBenchmarkSample,
    DocumentAnalysisTrendGateItem,
    DocumentAnalysisTrendMetricItem,
    DocumentAnalysisTrendResponse,
    DocumentAnalysisTrendSubAgentItem,
    DocumentAnalysisTrendToolItem,
    EvalTaskResponse,
    RagasCompareRequest,
    RagasCompareResponse,
    RagasCompareStrategyResult,
    RagasEvalRequest,
    ReplayCompareRequest,
    ReplayCompareResponse,
    ReplayStrategySummary,
)
from app.models.artifact import ReportArtifactContent
from app.models.task import TaskRequest
from app.rag.observability import TraceEvent, TraceRecorder
from app.services.eval_service_parts.compare_helpers import EvalCompareMixin
from app.services.eval_service_parts.ragas_reports import EvalRagasReportMixin
from app.services.eval_service_parts.task_runner import EvalTaskRunnerMixin
from app.services.query_service import QueryService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.services.task_service import TaskService

if TYPE_CHECKING:
    pass

DocumentAnalysisOutputFormat = Literal['markdown', 'json', 'markdown+json']


class DocumentAnalysisSubAgentTraceEntry(TypedDict):
    """单个子 Agent 在单条任务样本里的执行统计。"""

    run_count: int
    failure_count: int
    actions: dict[str, int]
    selected_tools: dict[str, int]
    failure_rate: float


class DocumentAnalysisSubAgentBreakdown(TypedDict):
    """聚合到 dashboard 维度的子 Agent 统计。"""

    run_count: int
    avg_runs_per_sample: float
    failure_count: int
    failure_rate: float
    avg_failure_count_per_sample: float
    actions: dict[str, int]
    selected_tools: dict[str, int]


class EvalService(EvalCompareMixin, EvalTaskRunnerMixin, EvalRagasReportMixin):
    """评测服务主类。

    对外主要提供跑单次评测、做多策略对比，以及执行 Document Analysis benchmark
    这几类能力。
    """

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        trace: TraceRecorder,
        query_service: QueryService,
        task_service: TaskService | None = None,
        persistence: SQLiteStateStore | None = None,
    ) -> None:
        """收好评测流程要用到的依赖。"""
        self.settings = settings
        self.state = state
        self.trace = trace
        self.query_service = query_service
        self.task_service = task_service
        self.persistence = persistence

    def create_task(self, payload: RagasEvalRequest) -> EvalTaskResponse:
        """创建并同步执行一次评测任务。"""
        # 先写入 running 态，确保调用链即使中途失败也能看到一条完整的任务记录。
        task = EvalTaskResponse(
            task_id=f'eval-{uuid4().hex[:8]}',
            status='running',
            summary='RAGAS 评测任务已创建，正在执行数据回放与指标计算。',
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            started_at=datetime.now(timezone.utc),
        )
        self.state.eval_tasks[task.task_id] = task.model_dump()
        self._persist_task(task)
        self.trace.record('ragas_task_created', payload.model_dump())

        try:
            completed = self._run_task(task.task_id, payload)
            self.state.eval_tasks[task.task_id] = completed.model_dump()
            self._persist_task(completed)
            return completed
        except Exception as exc:
            # 失败分支也要覆盖内存态和持久层，避免任务停留在“运行中”的假状态。
            failed_payload = dict(self.state.eval_tasks[task.task_id])
            failed_payload.update(
                {
                    'status': 'failed',
                    'summary': 'RAGAS 评测执行失败。',
                    'error': str(exc),
                    'completed_at': datetime.now(timezone.utc),
                }
            )
            failed = EvalTaskResponse(**failed_payload)
            self.state.eval_tasks[task.task_id] = failed.model_dump()
            self._persist_task(failed)
            self.trace.record('ragas_task_failed', {'task_id': task.task_id, 'reason': str(exc)})
            return failed

    def compare_tasks(self, payload: RagasCompareRequest) -> RagasCompareResponse:
        """一组一组跑策略，然后把对比结果汇总起来。"""
        strategies = payload.strategies
        self._validate_compare_strategies(strategies, payload.baseline_name)
        baseline_name = payload.baseline_name or strategies[0].name
        compare_id = f'cmp-{uuid4().hex[:8]}'

        results: list[RagasCompareStrategyResult] = []
        for strategy in strategies:
            task = self.create_task(
                RagasEvalRequest(
                    dataset_path=payload.dataset_path,
                    collection_name=payload.collection_name,
                    top_k=strategy.top_k or 5,
                    use_query_rewrite=strategy.use_query_rewrite,
                    use_multi_query=strategy.use_multi_query,
                    multi_query_count=strategy.multi_query_count,
                    use_multi_rewrite=strategy.use_multi_rewrite,
                    multi_rewrite_count=strategy.multi_rewrite_count,
                    use_hybrid_retrieval=strategy.use_hybrid_retrieval,
                    use_rerank=strategy.use_rerank,
                    use_hyde=strategy.use_hyde,
                    use_long_context_reorder=strategy.use_long_context_reorder,
                    use_parent_chunk_retrieval=strategy.use_parent_chunk_retrieval,
                    use_question_oriented_index=strategy.use_question_oriented_index,
                    use_corrective_rag=strategy.use_corrective_rag,
                    use_graph_rag=strategy.use_graph_rag,
                    graph_max_hops=strategy.graph_max_hops,
                    graph_top_k=strategy.graph_top_k or strategy.top_k or 5,
                    graph_entity_types=strategy.graph_entity_types,
                )
            )
            results.append(RagasCompareStrategyResult(strategy=strategy, task=task))

        metrics = self._build_compare_metrics(results, baseline_name)
        completed_count = sum(1 for item in results if item.task.status == 'completed')
        summary = (
            f'完成 {len(results)} 组策略对比，成功 {completed_count} 组，'
            f'基线策略为 {baseline_name}。'
        )
        completed_at = datetime.now(timezone.utc)
        response = RagasCompareResponse(
            compare_id=compare_id,
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            baseline_name=baseline_name,
            summary=summary,
            strategies=results,
            metrics=metrics,
            completed_at=completed_at,
        )
        result_path = self._write_compare_result_file(response)
        response.result_path = str(result_path)
        self.trace.record(
            'ragas_compare_completed',
            {
                'compare_id': compare_id,
                'collection_name': payload.collection_name,
                'strategy_count': len(results),
                'baseline_name': baseline_name,
                'metrics': {key: value.model_dump() for key, value in metrics.items()},
            },
        )
        return response

    def compare_replay(self, payload: ReplayCompareRequest) -> ReplayCompareResponse:
        """只回放查询链路并统计检索表现，适合不依赖 Ragas 的离线回归。"""
        strategies = payload.strategies
        self._validate_compare_strategies(strategies, payload.baseline_name)
        baseline_name = payload.baseline_name or strategies[0].name
        compare_id = f'replay-{uuid4().hex[:8]}'

        dataset_entries = self._load_eval_dataset(
            payload.dataset_path,
            payload.collection_name,
            default_use_query_rewrite=True,
            default_use_multi_query=False,
            default_multi_query_count=3,
            default_use_multi_rewrite=False,
            default_multi_rewrite_count=3,
            default_use_hybrid_retrieval=False,
            default_use_hyde=False,
            default_use_long_context_reorder=False,
            default_use_parent_chunk_retrieval=False,
            default_use_question_oriented_index=False,
            default_use_corrective_rag=False,
            default_use_graph_rag=False,
            default_graph_max_hops=1,
            default_graph_top_k=5,
            default_graph_entity_types=None,
        )

        summaries: list[ReplayStrategySummary] = []
        for strategy in strategies:
            summaries.append(self._replay_strategy(dataset_entries, payload.collection_name, strategy))

        completed_at = datetime.now(timezone.utc)
        metrics, bucket_metrics = self._build_replay_compare_metrics(summaries, baseline_name)
        response = ReplayCompareResponse(
            compare_id=compare_id,
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            baseline_name=baseline_name,
            summary=f'完成 {len(summaries)} 组回放对比，基线策略为 {baseline_name}。',
            strategies=summaries,
            metrics=metrics,
            bucket_metrics=bucket_metrics,
            completed_at=completed_at,
        )
        result_path = self._write_replay_compare_result_file(response)
        response.result_path = str(result_path)
        self.trace.record(
            'replay_compare_completed',
            {
                'compare_id': compare_id,
                'collection_name': payload.collection_name,
                'strategy_names': [item.strategy.name for item in summaries],
            },
        )
        return response

    def benchmark_document_analysis(self, payload: DocumentAnalysisBenchmarkRequest) -> DocumentAnalysisBenchmarkResponse:
        """执行 Document Analysis Agent 的 benchmark 回归。"""

        if self.task_service is None:
            raise bad_request_error(
                code='task_benchmark_unavailable',
                message='document analysis benchmark requires task service',
            )
        dataset_entries = self._load_document_analysis_benchmark_dataset(payload.dataset_path, payload.collection_name)
        benchmark_id = f'task-benchmark-{uuid4().hex[:8]}'
        samples: list[DocumentAnalysisBenchmarkSample] = []
        for index, item in enumerate(dataset_entries, start=1):
            trace_start = len(self.trace.events)
            created = self.task_service.create_document_analysis(
                payload=item['request'],
            )
            latest = created
            deadline = time() + payload.max_wait_seconds
            # benchmark 复用真实任务系统，所以这里按轮询方式等任务自然结束。
            while latest.status in {'queued', 'running'} and time() < deadline:
                sleep(payload.poll_interval_seconds)
                latest = self.task_service.get_task(created.task_id)
            task_trace = self._collect_task_trace_events(trace_start, created.task_id)
            if latest.status in {'queued', 'running'}:
                sample = DocumentAnalysisBenchmarkSample(
                    index=index,
                    instructions=item['request'].instructions,
                    collection_name=item['request'].collection_name,
                    doc_ids=item['request'].doc_ids,
                    task_id=created.task_id,
                    status='timeout',
                    expected_findings=item['expected_findings'],
                    expected_risks=item['expected_risks'],
                    step_trace=self._build_step_trace(task_trace),
                    tool_trace=self._build_tool_trace(latest),
                    sub_agent_trace=self._build_sub_agent_trace(latest),
                    artifact_trace=self._build_artifact_trace(latest),
                    error='task did not finish before timeout',
                )
            elif latest.status != 'completed' or latest.final_artifact is None:
                sample = DocumentAnalysisBenchmarkSample(
                    index=index,
                    instructions=item['request'].instructions,
                    collection_name=item['request'].collection_name,
                    doc_ids=item['request'].doc_ids,
                    task_id=created.task_id,
                    status=latest.status,
                    expected_findings=item['expected_findings'],
                    expected_risks=item['expected_risks'],
                    step_trace=self._build_step_trace(task_trace),
                    tool_trace=self._build_tool_trace(latest),
                    sub_agent_trace=self._build_sub_agent_trace(latest),
                    artifact_trace=self._build_artifact_trace(latest),
                    error=latest.failures[-1].message if latest.failures else 'task failed without error detail',
                )
            else:
                sample = self._score_document_analysis_sample(index, latest, item, task_trace)
            samples.append(sample)

        success_count = sum(1 for item in samples if item.status == 'completed')
        failed_count = len(samples) - success_count
        metrics = self._summarize_document_analysis_benchmark(samples)
        completed_at = datetime.now(timezone.utc)
        response = DocumentAnalysisBenchmarkResponse(
            benchmark_id=benchmark_id,
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            summary=(
                f'完成 {len(samples)} 条 Document Analysis benchmark，'
                f'成功 {success_count} 条，失败 {failed_count} 条。'
            ),
            sample_count=len(samples),
            success_count=success_count,
            failed_count=failed_count,
            metrics=metrics,
            samples=samples,
            completed_at=completed_at,
        )
        response.dashboard_summary = self._build_document_analysis_dashboard_summary(response)
        response.gate = self._build_document_analysis_benchmark_gate(payload, response)
        result_path = self._write_document_analysis_benchmark_result_file(response)
        response.result_path = str(result_path)
        self.trace.record(
            'document_analysis_benchmark_completed',
            {
                'benchmark_id': benchmark_id,
                'sample_count': len(samples),
                'success_count': success_count,
                'failed_count': failed_count,
                'metrics': metrics,
            },
        )
        return response

    def list_document_analysis_benchmark_reports(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        collection_name: str | None = None,
        gate_status: str | None = None,
        input_dir: str | None = None,
    ) -> DocumentAnalysisBenchmarkHistoryResponse:
        """按条件读取本地已落盘的 benchmark 报告列表。"""
        reports = self._load_document_analysis_report_records(
            input_dir=input_dir,
            collection_name=collection_name,
            gate_status=gate_status,
        )
        page = reports[offset: offset + limit]
        return DocumentAnalysisBenchmarkHistoryResponse(
            items=[self._build_document_analysis_report_summary(item) for item in page],
            total=len(reports),
            limit=limit,
            offset=offset,
        )

    def get_document_analysis_benchmark_report(
        self,
        benchmark_id: str,
        *,
        input_dir: str | None = None,
    ) -> DocumentAnalysisBenchmarkReportResponse:
        """读取单次 benchmark 报告。"""
        reports = self._load_document_analysis_report_records(input_dir=input_dir)
        for item in reports:
            result = item['result']
            if result.benchmark_id == benchmark_id or Path(item['path']).stem == benchmark_id:
                return DocumentAnalysisBenchmarkReportResponse(
                    report_path=item['path'],
                    report_mode='document_analysis_benchmark',
                    dashboard_summary=item['dashboard_summary'],
                    gate=item['gate'],
                    result=result,
                )
        raise FileNotFoundError(f'document analysis benchmark report not found: {benchmark_id}')

    def get_latest_document_analysis_dashboard(
        self,
        *,
        collection_name: str | None = None,
        input_dir: str | None = None,
    ) -> DocumentAnalysisBenchmarkReportResponse:
        """读取最近一次 benchmark 的 dashboard 和 gate 结果。"""
        reports = self._load_document_analysis_report_records(input_dir=input_dir, collection_name=collection_name)
        if not reports:
            raise FileNotFoundError('no document analysis benchmark reports found')
        latest = reports[0]
        return DocumentAnalysisBenchmarkReportResponse(
            report_path=latest['path'],
            report_mode='document_analysis_benchmark',
            dashboard_summary=latest['dashboard_summary'],
            gate=latest['gate'],
            result=latest['result'],
        )

    def get_document_analysis_trend(
        self,
        *,
        limit: int = 10,
        collection_name: str | None = None,
        input_dir: str | None = None,
    ) -> DocumentAnalysisTrendResponse:
        """聚合最近几次 benchmark 结果，生成趋势视图。"""
        reports = list(
            reversed(
                self._load_document_analysis_report_records(
                    input_dir=input_dir,
                    limit=limit,
                    collection_name=collection_name,
                )
            )
        )
        if not reports:
            raise FileNotFoundError('no document analysis benchmark reports found')
        latest = reports[-1]
        gate_counts: dict[str, int] = {}
        for item in reports:
            gate_status = item['gate'].status if item['gate'] is not None else 'unknown'
            gate_counts[gate_status] = gate_counts.get(gate_status, 0) + 1
        payload = {
            'generated_at': datetime.now().isoformat(),
            'report_count': len(reports),
            'latest_report_path': latest['path'],
            'latest_completed_at': latest['completed_at'],
            'latest_benchmark_id': latest['result'].benchmark_id,
            'latest_collection_name': latest['result'].collection_name or '-',
            'gate_counts': dict(sorted(gate_counts.items())),
            'gate_history': self._build_document_analysis_trend_gate_history(reports),
            'metric_trends': self._build_document_analysis_metric_trends(reports),
            'tool_trends': self._build_document_analysis_tool_trends(reports),
            'sub_agent_trends': self._build_document_analysis_sub_agent_trends(reports),
            'latest_dashboard_summary': latest['dashboard_summary'],
            'latest_gate': latest['gate'],
        }
        payload['insights'] = self._build_document_analysis_trend_insights(payload)
        return DocumentAnalysisTrendResponse(**payload)

    def get_document_analysis_baseline_resolution(
        self,
        *,
        collection_name: str,
        instructions: str = '',
        output_format: DocumentAnalysisOutputFormat = 'markdown',
    ) -> DocumentAnalysisBaselineResolutionResponse:
        """解析某个文档分析请求当前会命中的 baseline。"""
        if not collection_name.strip():
            raise bad_request_error('baseline_collection_required', 'collection_name is required')
        evaluation_harness = self._require_document_analysis_evaluation_harness()
        request = TaskRequest(
            collection_name=collection_name.strip(),
            doc_ids=[],
            instructions=instructions.strip() or collection_name.strip(),
            output_format=output_format,
        )
        response = evaluation_harness.describe_baseline_resolution(request)
        return self._apply_managed_selection_to_resolution(response, request)

    def get_document_analysis_baseline_registry(
        self,
        *,
        collection_name: str,
        instructions: str = '',
        output_format: DocumentAnalysisOutputFormat = 'markdown',
        kind: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> DocumentAnalysisBaselineRegistryResponse:
        """列出某个文档分析请求当前可见的 baseline 注册表。"""
        if not collection_name.strip():
            raise bad_request_error('baseline_collection_required', 'collection_name is required')
        evaluation_harness = self._require_document_analysis_evaluation_harness()
        request = TaskRequest(
            collection_name=collection_name.strip(),
            doc_ids=[],
            instructions=instructions.strip() or collection_name.strip(),
            output_format=output_format,
        )
        response = evaluation_harness.list_baseline_registry(
            request,
            kind=kind,
            limit=limit,
            offset=offset,
        )
        return self._apply_managed_selection_to_registry(response, request)

    def get_document_analysis_run_trend(
        self,
        *,
        collection_name: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """聚合最近几次在线任务运行的趋势。"""
        evaluation_harness = self._require_document_analysis_evaluation_harness()
        return evaluation_harness.summarize_recent_trends(collection_name=collection_name, limit=limit)

    def compare_document_analysis_task_runs(
        self,
        *,
        task_id: str,
        baseline_task_id: str,
    ) -> dict[str, Any]:
        """对比两次文档分析任务运行结果。"""
        task_service = self._require_document_analysis_task_service()
        evaluation_harness = self._require_document_analysis_evaluation_harness()
        task = task_service.get_task(task_id)
        baseline_task = task_service.get_task(baseline_task_id)
        regression = evaluation_harness.compare_task_runs(task, baseline_task)
        task_scorecard = task.evaluation_scorecard or evaluation_harness.evaluate_task(task)[0]
        baseline_scorecard = baseline_task.evaluation_scorecard or evaluation_harness.evaluate_task(baseline_task)[0]
        return {
            'task_id': task.task_id,
            'baseline_task_id': baseline_task.task_id,
            'collection_name': task.request.collection_name,
            'scorecard_version': task_scorecard.scorecard_version,
            'task_overall_score': task_scorecard.overall_score,
            'baseline_overall_score': baseline_scorecard.overall_score,
            'task_runtime_metadata': task_scorecard.runtime_metadata,
            'baseline_runtime_metadata': baseline_scorecard.runtime_metadata,
            'regression': regression.model_dump(mode='json'),
        }

    def list_managed_document_analysis_baselines(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        collection_name: str | None = None,
        binding_policy_name: str | None = None,
        binding_instruction_substring: str | None = None,
        review_status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ManagedDocumentAnalysisBaselineListResponse:
        """列出显式注册到持久化注册表的 baseline。"""
        items = self._load_managed_document_analysis_baseline_entries()
        if kind is not None and kind.strip():
            items = [item for item in items if item.kind == kind.strip()]
        if status is not None and status.strip():
            items = [item for item in items if item.status == status.strip()]
        if collection_name is not None and collection_name.strip():
            items = [item for item in items if (item.collection_name or '').strip() == collection_name.strip()]
        if binding_policy_name is not None and binding_policy_name.strip():
            items = [item for item in items if (item.binding_policy_name or '').strip() == binding_policy_name.strip()]
        if binding_instruction_substring is not None and binding_instruction_substring.strip():
            items = [
                item
                for item in items
                if (item.binding_instruction_substring or '').strip() == binding_instruction_substring.strip()
            ]
        if review_status is not None and review_status.strip():
            items = [item for item in items if item.review_status == review_status.strip()]
        total = len(items)
        paged = items[offset: offset + limit]
        return ManagedDocumentAnalysisBaselineListResponse(
            total=total,
            limit=limit,
            offset=offset,
            kind=kind.strip() if kind is not None and kind.strip() else None,
            status=status.strip() if status is not None and status.strip() else None,
            collection_name=collection_name.strip() if collection_name is not None and collection_name.strip() else None,
            binding_policy_name=(
                binding_policy_name.strip()
                if binding_policy_name is not None and binding_policy_name.strip()
                else None
            ),
            binding_instruction_substring=(
                binding_instruction_substring.strip()
                if binding_instruction_substring is not None and binding_instruction_substring.strip()
                else None
            ),
            review_status=review_status.strip() if review_status is not None and review_status.strip() else None,
            items=paged,
        )

    def list_managed_document_analysis_baseline_audits(
        self,
        *,
        entry_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ManagedDocumentAnalysisBaselineAuditListResponse:
        """列出 managed baseline 的审计日志。"""
        audits = self._load_managed_document_analysis_baseline_audits()
        if entry_id is not None and entry_id.strip():
            audits = [item for item in audits if item.entry_id == entry_id.strip()]
        page = audits[offset: offset + limit]
        return ManagedDocumentAnalysisBaselineAuditListResponse(
            total=len(audits),
            limit=limit,
            offset=offset,
            entry_id=entry_id.strip() if entry_id is not None and entry_id.strip() else None,
            items=page,
        )

    def register_document_analysis_baseline(
        self,
        payload: ManagedDocumentAnalysisBaselineRegisterRequest,
    ) -> ManagedDocumentAnalysisBaselineEntry:
        """把当前可见的 baseline 候选显式写入本地注册表。"""
        task_service = self._require_document_analysis_task_service()
        evaluation_harness = self._require_document_analysis_evaluation_harness()
        request = TaskRequest(
            collection_name=payload.collection_name.strip(),
            doc_ids=[],
            instructions=(payload.instructions or '').strip() or payload.collection_name.strip(),
            output_format=payload.output_format,
            organization_id=payload.organization_id,
            tenant_id=payload.tenant_id,
            requester_role=payload.actor_role,
        )
        self._require_baseline_registry_role(payload.actor_role, allowed_roles={'admin', 'owner', 'reviewer'})
        profile = task_service.policy_engine.resolve_profile(request)
        normalized_binding_instruction_substring = (
            (payload.binding_instruction_substring or '').strip().lower() or None
        )
        candidate_registry = evaluation_harness.list_baseline_registry(
            request,
            kind=payload.kind,
            limit=500,
            offset=0,
        )
        candidate = next((item for item in candidate_registry.items if item.reference == payload.reference.strip()), None)
        if candidate is None:
            raise bad_request_error(
                'baseline_candidate_not_found',
                'baseline candidate not found for current request',
                {
                    'collection_name': payload.collection_name,
                    'kind': payload.kind,
                    'reference': payload.reference,
                },
            )
        existing = self._load_managed_document_analysis_baseline_entries()
        duplicate = next(
            (
                item
                for item in existing
                if item.kind == candidate.kind
                and item.reference == candidate.reference
                and item.collection_name == candidate.collection_name
                and (item.binding_policy_name or '').strip() == profile.name
                and (item.binding_instruction_substring or '').strip().lower() == (normalized_binding_instruction_substring or '')
            ),
            None,
        )
        if duplicate is not None:
            raise bad_request_error(
                'baseline_registry_duplicate',
                'baseline already registered',
                {'entry_id': duplicate.entry_id, 'reference': duplicate.reference, 'kind': duplicate.kind},
            )
        now = datetime.now(timezone.utc)
        entry = ManagedDocumentAnalysisBaselineEntry(
            entry_id=f'baseline-{uuid4().hex[:12]}',
            kind=candidate.kind,
            reference=candidate.reference,
            task_id=candidate.task_id,
            collection_name=candidate.collection_name,
            organization_id=payload.organization_id,
            tenant_id=payload.tenant_id,
            policy_name=candidate.policy_name,
            policy_version=candidate.policy_version,
            scorecard_version=candidate.scorecard_version,
            overall_score=candidate.overall_score,
            coverage_score=candidate.coverage_score,
            grounding_score=candidate.grounding_score,
            review_score=candidate.review_score,
            unsupported_claim_rate=candidate.unsupported_claim_rate,
            generated_at=candidate.generated_at,
            binding_policy_name=profile.name,
            binding_instruction_substring=normalized_binding_instruction_substring,
            status=payload.status,
            review_status=payload.review_status,
            review_note=(payload.review_note or '').strip() or None,
            created_by=(payload.actor or '').strip() or None,
            updated_by=(payload.actor or '').strip() or None,
            reviewed_by=(payload.actor or '').strip() or None if payload.review_status == 'approved' else None,
            reviewed_at=now if payload.review_status == 'approved' else None,
            note=(payload.note or '').strip() or None,
            created_at=now,
            updated_at=now,
        )
        existing.append(entry)
        saved_items, archived_entry_ids = self._enforce_single_active_managed_baseline(
            existing,
            target_entry_id=entry.entry_id,
            collection_name=entry.collection_name,
            binding_policy_name=entry.binding_policy_name,
            binding_instruction_substring=entry.binding_instruction_substring,
            status=entry.status,
        )
        self._save_managed_document_analysis_baseline_entries(saved_items)
        self._record_managed_baseline_audit(
            action='created',
            entry=entry,
            actor=payload.actor,
            actor_role=payload.actor_role,
            summary='创建 managed baseline 记录。',
        )
        for archived_entry_id in archived_entry_ids:
            archived_entry = next((item for item in saved_items if item.entry_id == archived_entry_id), None)
            if archived_entry is not None:
                self._record_managed_baseline_audit(
                    action='archived',
                    entry=archived_entry,
                    actor=payload.actor,
                    actor_role=payload.actor_role,
                    summary='新 active baseline 生效，旧记录自动归档。',
                )
        self.trace.record(
            'document_analysis_baseline_registered',
            {
                'entry_id': entry.entry_id,
                'kind': entry.kind,
                'reference': entry.reference,
                'status': entry.status,
                'archived_entry_ids': archived_entry_ids,
            },
        )
        entry = next(item for item in saved_items if item.entry_id == entry.entry_id)
        return entry

    def update_managed_document_analysis_baseline(
        self,
        entry_id: str,
        payload: ManagedDocumentAnalysisBaselineUpdateRequest,
    ) -> ManagedDocumentAnalysisBaselineEntry:
        """更新持久化注册表中的 baseline 状态、审核状态或备注。"""
        if payload.review_status is not None:
            self._require_baseline_registry_role(payload.actor_role, allowed_roles={'admin', 'owner', 'reviewer'})
        else:
            self._require_baseline_registry_role(payload.actor_role, allowed_roles={'admin', 'owner', 'reviewer'})
        items = self._load_managed_document_analysis_baseline_entries()
        for index, item in enumerate(items):
            if item.entry_id != entry_id:
                continue
            review_status = payload.review_status if payload.review_status is not None else item.review_status
            now = datetime.now(timezone.utc)
            updated = item.model_copy(
                update={
                    'status': payload.status if payload.status is not None else item.status,
                    'binding_instruction_substring': (
                        (payload.binding_instruction_substring or '').strip().lower() or None
                        if payload.binding_instruction_substring is not None
                        else item.binding_instruction_substring
                    ),
                    'review_status': review_status,
                    'review_note': (
                        (payload.review_note or '').strip() or None if payload.review_note is not None else item.review_note
                    ),
                    'updated_by': (payload.actor or '').strip() or item.updated_by,
                    'reviewed_by': (
                        (payload.actor or '').strip() or item.reviewed_by
                        if payload.review_status is not None
                        else item.reviewed_by
                    ),
                    'reviewed_at': now if payload.review_status is not None else item.reviewed_at,
                    'note': (payload.note or '').strip() or None if payload.note is not None else item.note,
                    'updated_at': now,
                }
            )
            items[index] = updated
            saved_items, archived_entry_ids = self._enforce_single_active_managed_baseline(
                items,
                target_entry_id=updated.entry_id,
                collection_name=updated.collection_name,
                binding_policy_name=updated.binding_policy_name,
                binding_instruction_substring=updated.binding_instruction_substring,
                status=updated.status,
            )
            self._save_managed_document_analysis_baseline_entries(saved_items)
            self._record_managed_baseline_audit(
                action='updated',
                entry=updated,
                actor=payload.actor,
                actor_role=payload.actor_role,
                summary='更新 managed baseline 记录。',
            )
            for archived_entry_id in archived_entry_ids:
                archived_entry = next((item for item in saved_items if item.entry_id == archived_entry_id), None)
                if archived_entry is not None:
                    self._record_managed_baseline_audit(
                        action='archived',
                        entry=archived_entry,
                        actor=payload.actor,
                        actor_role=payload.actor_role,
                        summary='active baseline 切换导致原记录归档。',
                    )
            self.trace.record(
                'document_analysis_baseline_updated',
                {
                    'entry_id': updated.entry_id,
                    'kind': updated.kind,
                    'reference': updated.reference,
                    'status': updated.status,
                    'archived_entry_ids': archived_entry_ids,
                },
            )
            return next(saved_item for saved_item in saved_items if saved_item.entry_id == updated.entry_id)
        raise FileNotFoundError(f'document analysis baseline registry entry not found: {entry_id}')

    def delete_managed_document_analysis_baseline(
        self,
        entry_id: str,
        *,
        actor: str | None = None,
        actor_role: str | None = None,
    ) -> None:
        """删除持久化注册表中的 baseline 记录。"""
        self._require_baseline_registry_role(actor_role, allowed_roles={'admin', 'owner'})
        items = self._load_managed_document_analysis_baseline_entries()
        deleted = next((item for item in items if item.entry_id == entry_id), None)
        remaining = [item for item in items if item.entry_id != entry_id]
        if len(remaining) == len(items):
            raise FileNotFoundError(f'document analysis baseline registry entry not found: {entry_id}')
        self._save_managed_document_analysis_baseline_entries(remaining)
        if deleted is not None:
            self._record_managed_baseline_audit(
                action='deleted',
                entry=deleted,
                actor=actor,
                actor_role=actor_role,
                summary='删除 managed baseline 记录。',
            )
        self.trace.record(
            'document_analysis_baseline_deleted',
            {
                'entry_id': entry_id,
            },
        )

    def _require_document_analysis_task_service(self):
        """确保当前实例已装配文档分析任务服务。

        Returns:
            已装配的任务服务实例。
        """
        if self.task_service is None:
            raise bad_request_error(
                'document_analysis_baseline_unavailable',
                'document analysis baseline requires task service',
            )
        return self.task_service

    def _require_document_analysis_evaluation_harness(self):
        """确保文档分析评测 harness 可用。

        Returns:
            任务运行时暴露的文档分析评测 harness。
        """
        task_service = self._require_document_analysis_task_service()
        runtime = getattr(task_service, 'runtime', None)
        orchestrator = getattr(runtime, 'orchestrator', None)
        evaluation_harness = getattr(orchestrator, 'evaluation_harness', None)
        if evaluation_harness is None:
            raise bad_request_error(
                'document_analysis_evaluation_unavailable',
                'document analysis evaluation harness is unavailable',
            )
        return evaluation_harness

    def _load_managed_document_analysis_baseline_entries(self) -> list[ManagedDocumentAnalysisBaselineEntry]:
        """加载 managed baseline 注册表条目，并按更新时间倒序返回。"""
        items: list[ManagedDocumentAnalysisBaselineEntry] = []
        raw_items: list[dict[str, Any]] = []
        if self.persistence is not None:
            raw_items = self.persistence.list_managed_baselines()
        elif self.settings.document_analysis_baseline_registry_path.exists():
            try:
                raw_payload = json.loads(self.settings.document_analysis_baseline_registry_path.read_text(encoding='utf-8'))
                payload_items = raw_payload.get('items', []) if isinstance(raw_payload, dict) else []
                if isinstance(payload_items, list):
                    raw_items = [item for item in payload_items if isinstance(item, dict)]
            except Exception:
                raw_items = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                items.append(ManagedDocumentAnalysisBaselineEntry.model_validate(item))
            except Exception:
                continue
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items

    def _save_managed_document_analysis_baseline_entries(
        self,
        items: list[ManagedDocumentAnalysisBaselineEntry],
    ) -> None:
        """保存 managed baseline 注册表条目。

        Args:
            items: 需要落盘的 baseline 条目列表。
        """
        if self.persistence is not None:
            existing_ids = {item.entry_id for item in items}
            persisted_items = self.persistence.list_managed_baselines()
            for persisted in persisted_items:
                persisted_entry_id = str(persisted.get('entry_id') or '').strip()
                if persisted_entry_id and persisted_entry_id not in existing_ids:
                    self.persistence.delete_managed_baseline(persisted_entry_id)
            for item in items:
                self.persistence.upsert_managed_baseline(item.model_dump(mode='python'))
            return
        path = self.settings.document_analysis_baseline_registry_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'version': 'v1', 'items': [item.model_dump(mode='json') for item in items]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _load_managed_document_analysis_baseline_audits(self) -> list[ManagedDocumentAnalysisBaselineAuditEntry]:
        """读取 managed baseline 审计日志，并按时间倒序返回。"""
        if self.persistence is None:
            return []
        items: list[ManagedDocumentAnalysisBaselineAuditEntry] = []
        for payload in self.persistence.list_managed_baseline_audits():
            try:
                items.append(ManagedDocumentAnalysisBaselineAuditEntry.model_validate(payload))
            except Exception:
                continue
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def _record_managed_baseline_audit(
        self,
        *,
        action: str,
        entry: ManagedDocumentAnalysisBaselineEntry,
        actor: str | None,
        actor_role: str | None,
        summary: str,
    ) -> None:
        """为 managed baseline 变更追加一条审计记录。"""
        if self.persistence is None:
            return
        audit = ManagedDocumentAnalysisBaselineAuditEntry(
            audit_id=f'baseline-audit-{uuid4().hex[:12]}',
            entry_id=entry.entry_id,
            action=cast(Any, action),
            actor=(actor or '').strip() or None,
            actor_role=(actor_role or '').strip().lower() or None,
            summary=summary,
            snapshot=entry,
            created_at=datetime.now(timezone.utc),
        )
        self.persistence.append_managed_baseline_audit(audit.model_dump(mode='python'))

    def _require_baseline_registry_role(self, actor_role: str | None, *, allowed_roles: set[str]) -> None:
        """校验当前操作者角色是否允许管理 baseline 注册表。"""
        normalized_role = (actor_role or '').strip().lower()
        if not normalized_role:
            return
        if normalized_role in allowed_roles:
            return
        raise bad_request_error(
            'baseline_registry_role_forbidden',
            'actor role is not allowed to manage baseline registry',
            {'actor_role': actor_role, 'allowed_roles': sorted(allowed_roles)},
        )

    def _enforce_single_active_managed_baseline(
        self,
        items: list[ManagedDocumentAnalysisBaselineEntry],
        *,
        target_entry_id: str,
        collection_name: str | None,
        binding_policy_name: str | None,
        binding_instruction_substring: str | None,
        status: str,
    ) -> tuple[list[ManagedDocumentAnalysisBaselineEntry], list[str]]:
        """保证同一绑定范围内最多只有一个 active managed baseline。

        Returns:
            第一项为归一化后的条目列表，第二项为被自动归档的条目 ID 列表。
        """
        if status != 'active':
            return items, []
        normalized_collection_name = (collection_name or '').strip()
        normalized_binding_policy_name = (binding_policy_name or '').strip()
        normalized_binding_instruction_substring = (binding_instruction_substring or '').strip().lower()
        archived_entry_ids: list[str] = []
        normalized_items: list[ManagedDocumentAnalysisBaselineEntry] = []
        for item in items:
            same_scope = (
                (item.collection_name or '').strip() == normalized_collection_name
                and (item.binding_policy_name or '').strip() == normalized_binding_policy_name
                and (item.binding_instruction_substring or '').strip().lower() == normalized_binding_instruction_substring
            )
            if item.entry_id != target_entry_id and same_scope and item.status == 'active':
                archived_entry_ids.append(item.entry_id)
                normalized_items.append(
                    item.model_copy(
                        update={
                            'status': 'archived',
                            'updated_at': datetime.now(timezone.utc),
                        }
                    )
                )
                continue
            normalized_items.append(item)
        return normalized_items, archived_entry_ids

    def _find_matching_active_managed_baseline(
        self,
        request: TaskRequest,
    ) -> ManagedDocumentAnalysisBaselineEntry | None:
        """为任务请求匹配最合适的 active managed baseline。"""
        task_service = self._require_document_analysis_task_service()
        request_policy_name = task_service.policy_engine.resolve_profile(request).name
        normalized_instructions = request.instructions.strip().lower()
        exact_matches: list[ManagedDocumentAnalysisBaselineEntry] = []
        fallback_matches: list[ManagedDocumentAnalysisBaselineEntry] = []
        for item in self._load_managed_document_analysis_baseline_entries():
            if item.status != 'active' or item.review_status != 'approved':
                continue
            if (item.collection_name or '').strip() != request.collection_name:
                continue
            if item.organization_id is not None and item.organization_id != request.organization_id:
                continue
            if item.tenant_id is not None and item.tenant_id != request.tenant_id:
                continue
            if (item.binding_policy_name or '').strip() != request_policy_name:
                continue
            binding_substring = (item.binding_instruction_substring or '').strip().lower()
            if binding_substring:
                if binding_substring in normalized_instructions:
                    exact_matches.append(item)
                continue
            fallback_matches.append(item)
        if exact_matches:
            exact_matches.sort(
                key=lambda item: (
                    len((item.binding_instruction_substring or '').strip()),
                    item.updated_at.timestamp(),
                ),
                reverse=True,
            )
            return exact_matches[0]
        return fallback_matches[0] if fallback_matches else None

    def _apply_managed_selection_to_resolution(
        self,
        response: DocumentAnalysisBaselineResolutionResponse,
        request: TaskRequest,
    ) -> DocumentAnalysisBaselineResolutionResponse:
        """把 managed baseline 命中结果回填到 baseline 解析响应中。"""
        managed_entry = self._find_matching_active_managed_baseline(request)
        if managed_entry is None:
            return response
        candidates = [self._attach_managed_metadata(candidate, managed_entry) for candidate in response.candidates]
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.kind == managed_entry.kind and candidate.reference == managed_entry.reference
            ),
            None,
        )
        if selected is None:
            selected = self._attach_managed_metadata(managed_entry.model_copy(), managed_entry)
            candidates.append(selected)
        return response.model_copy(
            update={
                'selected_baseline': selected,
                'candidates': candidates,
            }
        )

    def _apply_managed_selection_to_registry(
        self,
        response: DocumentAnalysisBaselineRegistryResponse,
        request: TaskRequest,
    ) -> DocumentAnalysisBaselineRegistryResponse:
        """把 managed baseline 命中结果回填到注册表响应中。"""
        managed_entry = self._find_matching_active_managed_baseline(request)
        if managed_entry is None:
            return response
        selected = response.selected_baseline
        items = []
        for item in response.items:
            is_managed_match = item.kind == managed_entry.kind and item.reference == managed_entry.reference
            updated_item = item.model_copy(
                update={
                    'selected': is_managed_match,
                    'managed_entry_id': managed_entry.entry_id if is_managed_match else item.managed_entry_id,
                    'managed_status': managed_entry.status if is_managed_match else item.managed_status,
                }
            )
            items.append(updated_item)
            if is_managed_match:
                selected = updated_item
        return response.model_copy(update={'selected_baseline': selected, 'items': items})

    def _attach_managed_metadata(
        self,
        candidate,
        managed_entry: ManagedDocumentAnalysisBaselineEntry,
    ):
        """在候选 baseline 与 managed baseline 匹配时附加托管元数据。"""
        if candidate.kind != managed_entry.kind or candidate.reference != managed_entry.reference:
            return candidate
        return candidate.model_copy(
            update={
                'managed_entry_id': managed_entry.entry_id,
                'managed_status': managed_entry.status,
            }
        )

    def _load_document_analysis_benchmark_dataset(
        self,
        dataset_path: str,
        default_collection_name: str | None,
    ) -> list[dict[str, Any]]:
        """读取并规整文档分析 benchmark 数据集。

        Args:
            dataset_path: 数据集文件路径。
            default_collection_name: 样本缺省集合名称。

        Returns:
            规整后的 benchmark 样本列表。
        """
        path = Path(dataset_path)
        if not path.exists():
            raise FileNotFoundError(f'benchmark dataset not found: {dataset_path}')
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, list):
            raise ValueError('document analysis benchmark dataset must be a JSON array')

        dataset_entries: list[dict[str, Any]] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f'第 {index} 条样本不是对象')
            collection_name = str(item.get('collection_name') or default_collection_name or '').strip()
            instructions = str(item.get('instructions') or '').strip()
            if not collection_name or not instructions:
                raise ValueError(f'第 {index} 条样本缺少 collection_name 或 instructions')
            dataset_entries.append(
                {
                    'request': TaskRequest(
                        collection_name=collection_name,
                        doc_ids=[str(doc_id) for doc_id in item.get('doc_ids', []) if str(doc_id).strip()],
                        instructions=instructions,
                        output_format=_normalize_document_analysis_output_format(item.get('output_format')),
                    ),
                    'bucket': str(item.get('bucket') or item.get('category') or item.get('type') or 'default').strip() or 'default',
                    'expected_findings': [str(value).strip() for value in item.get('expected_findings', []) if str(value).strip()],
                    'expected_risks': [str(value).strip() for value in item.get('expected_risks', []) if str(value).strip()],
                    'focus_dimensions': [str(value).strip() for value in item.get('focus_dimensions', []) if str(value).strip()],
                    'key_evidence_points': [str(value).strip() for value in item.get('key_evidence_points', []) if str(value).strip()],
                    'forbidden_claims': [str(value).strip() for value in item.get('forbidden_claims', []) if str(value).strip()],
                }
            )
        return dataset_entries

    def _load_document_analysis_report_records(
        self,
        *,
        input_dir: str | None = None,
        limit: int | None = None,
        collection_name: str | None = None,
        gate_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取本地 benchmark 报告文件，并按筛选条件返回报告记录。"""
        base_dir = Path(input_dir).expanduser().resolve() if input_dir else self.settings.eval_dir
        if not base_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(base_dir.glob('*.json')):
            raw_payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(raw_payload, dict) or raw_payload.get('report_mode') != 'document_analysis_benchmark':
                continue
            result_payload = raw_payload.get('result')
            dashboard_payload = raw_payload.get('dashboard_summary')
            gate_payload = raw_payload.get('gate')
            if not isinstance(result_payload, dict):
                continue
            try:
                result = DocumentAnalysisBenchmarkResponse.model_validate(result_payload)
                dashboard_summary = (
                    DocumentAnalysisDashboardSummary.model_validate(dashboard_payload)
                    if isinstance(dashboard_payload, dict)
                    else None
                )
                gate = (
                    DocumentAnalysisBenchmarkGate.model_validate(gate_payload)
                    if isinstance(gate_payload, dict)
                    else None
                )
            except Exception:
                continue
            completed_at = str(
                result.completed_at.isoformat()
                if isinstance(result.completed_at, datetime)
                else result_payload.get('completed_at') or ''
            )
            normalized_collection_name = str(collection_name or '').strip()
            if normalized_collection_name and str(result.collection_name or '').strip() != normalized_collection_name:
                continue
            normalized_gate_status = str(gate_status or '').strip()
            if normalized_gate_status and (gate.status if gate is not None else 'unknown') != normalized_gate_status:
                continue
            records.append(
                {
                    'path': str(path),
                    'result': result,
                    'dashboard_summary': dashboard_summary,
                    'gate': gate,
                    'completed_at': completed_at,
                }
            )
        records.sort(key=lambda item: item['completed_at'], reverse=True)
        if limit is not None and limit > 0:
            return records[:limit]
        return records

    def _build_document_analysis_report_summary(
        self,
        item: dict[str, Any],
    ) -> DocumentAnalysisBenchmarkReportSummary:
        """把单条报告记录压缩为列表展示所需的摘要对象。"""
        result: DocumentAnalysisBenchmarkResponse = item['result']
        dashboard: DocumentAnalysisDashboardSummary | None = item['dashboard_summary']
        gate: DocumentAnalysisBenchmarkGate | None = item['gate']
        return DocumentAnalysisBenchmarkReportSummary(
            benchmark_id=result.benchmark_id,
            collection_name=result.collection_name,
            completed_at=item['completed_at'],
            gate_status=gate.status if gate is not None else 'unknown',
            result_path=item['path'],
            sample_count=result.sample_count,
            success_rate=dashboard.success_rate if dashboard is not None else 0.0,
            avg_score=dashboard.avg_score if dashboard is not None else 0.0,
            avg_evidence_coverage=dashboard.avg_evidence_coverage if dashboard is not None else 0.0,
            avg_evidence_usability_score=dashboard.avg_evidence_usability_score if dashboard is not None else 0.0,
        )

    def _score_document_analysis_sample(
        self,
        index: int,
        task,
        item: dict[str, Any],
        task_trace: list[TraceEvent],
    ) -> DocumentAnalysisBenchmarkSample:
        """计算单条文档分析 benchmark 样本的质量评分与观测指标。"""
        artifact = task.final_artifact
        assert artifact is not None
        content = artifact.content
        findings_hit_rate = self._keyword_hit_rate(content, item['expected_findings'])
        risks_hit_rate = self._risk_hit_rate(content, item['expected_risks'])
        focus_dimension_hit_rate = self._focus_dimension_hit_rate(content, item['focus_dimensions'])
        key_evidence_hit_rate = self._key_evidence_hit_rate(content, item['key_evidence_points'])
        artifact_completeness = self._artifact_completeness(content)
        evidence_summary = self._extract_evidence_summary(task)
        evidence_coverage = evidence_summary['coverage_score']
        evidence_gap_count = len(evidence_summary['missing_aspects'])
        unsupported_claim_count = len(artifact.review.unsupported_claims) if artifact.review is not None else 0
        review_note_count = len(artifact.review.review_notes) if artifact.review is not None else 0
        forbidden_claim_hit_count = self._forbidden_claim_hit_count(content, item['forbidden_claims'])
        evidence_ids = {evidence.citation_id for evidence in content.evidence}
        grounded_finding_ratio = self._citation_grounding_ratio(content.key_findings, evidence_ids)
        grounded_risk_ratio = self._citation_grounding_ratio(content.risks, evidence_ids)
        evidence_usability_score = round(
            (
                evidence_coverage * 0.35
                + key_evidence_hit_rate * 0.25
                + grounded_finding_ratio * 0.15
                + grounded_risk_ratio * 0.15
                + focus_dimension_hit_rate * 0.10
            ),
            4,
        )
        unsupported_claim_score = 1.0 if unsupported_claim_count == 0 else 0.0
        hallucination_penalty = 0.0 if forbidden_claim_hit_count > 0 else 1.0
        retrieval_trace = self._extract_retrieval_trace(task_trace)
        score = round(
            (findings_hit_rate * 0.3)
            + (risks_hit_rate * 0.25)
            + (artifact_completeness * 0.2)
            + (evidence_coverage * 0.15)
            + (unsupported_claim_score * 0.05)
            + (hallucination_penalty * 0.05),
            4,
        )
        tool_error_count = sum(1 for record in task.tool_call_history if record.status == 'error')
        estimated_cost_units = self._estimate_task_cost_units(task)
        return DocumentAnalysisBenchmarkSample(
            index=index,
            instructions=task.request.instructions,
            collection_name=task.request.collection_name,
            bucket=item['bucket'],
            doc_ids=task.request.doc_ids,
            focus_dimensions=item['focus_dimensions'],
            key_evidence_points=item['key_evidence_points'],
            forbidden_claims=item['forbidden_claims'],
            task_id=task.task_id,
            status=task.status,
            score=score,
            findings_hit_rate=findings_hit_rate,
            risks_hit_rate=risks_hit_rate,
            focus_dimension_hit_rate=focus_dimension_hit_rate,
            key_evidence_hit_rate=key_evidence_hit_rate,
            artifact_completeness=artifact_completeness,
            evidence_coverage=evidence_coverage,
            grounded_finding_ratio=grounded_finding_ratio,
            grounded_risk_ratio=grounded_risk_ratio,
            evidence_usability_score=evidence_usability_score,
            unsupported_claim_count=unsupported_claim_count,
            plan_version=max(1, int(task.plan_version or 1)),
            artifact_version_count=len(task.artifact_ids),
            review_passed=artifact.review.passed if artifact.review is not None else True,
            review_replan_count=sum(1 for revision in task.plan_revisions if revision.trigger == 'review_failed'),
            step_count=int(task.metrics.step_count),
            tool_calls=int(task.metrics.tool_calls),
            tool_error_count=tool_error_count,
            sub_agent_run_count=len(task.sub_agent_runs),
            sub_agent_failure_count=sum(1 for item in task.sub_agent_runs if item.status == 'failed'),
            latency_ms=int(task.metrics.latency_ms),
            estimated_cost_units=estimated_cost_units,
            evidence_count=len(content.evidence),
            evidence_gap_count=evidence_gap_count,
            open_question_count=len(content.open_questions),
            review_note_count=review_note_count,
            retrieval_mode=retrieval_trace['retrieval_mode'],
            rerank_mode=retrieval_trace['rerank_mode'],
            retrieval_candidate_count=retrieval_trace['retrieval_candidate_count'],
            retrieval_selected_count=retrieval_trace['retrieval_selected_count'],
            step_trace=self._build_step_trace(task_trace),
            tool_trace=self._build_tool_trace(task),
            sub_agent_trace=self._build_sub_agent_trace(task),
            artifact_trace=self._build_artifact_trace(task),
            expected_findings=item['expected_findings'],
            expected_risks=item['expected_risks'],
            forbidden_claim_hit_count=forbidden_claim_hit_count,
        )

    def _keyword_hit_rate(self, content: ReportArtifactContent, expected_keywords: list[str]) -> float:
        """计算预期关键词在摘要和关键发现中的命中率。"""
        if not expected_keywords:
            return 1.0
        corpus = '\n'.join(
            [
                content.summary,
                content.report_markdown or '',
                *[item.title for item in content.key_findings],
                *[item.summary for item in content.key_findings],
            ]
        ).lower()
        hit_count = sum(1 for keyword in expected_keywords if keyword.lower() in corpus)
        return round(hit_count / max(1, len(expected_keywords)), 4)

    def _risk_hit_rate(self, content: ReportArtifactContent, expected_keywords: list[str]) -> float:
        """计算风险相关关键词在风险结论中的命中率。"""
        if not expected_keywords:
            return 1.0
        corpus = '\n'.join(
            [item.title for item in content.risks] + [item.description for item in content.risks]
        ).lower()
        hit_count = sum(1 for keyword in expected_keywords if keyword.lower() in corpus)
        return round(hit_count / max(1, len(expected_keywords)), 4)

    def _artifact_completeness(self, content: ReportArtifactContent) -> float:
        """根据摘要、报告和证据等关键字段估算产物完整度。"""
        checks = [
            bool(content.summary.strip()),
            bool(content.key_findings),
            content.report_markdown is not None,
            content.report_json is not None,
            bool(content.evidence),
        ]
        return round(sum(1 for item in checks if item) / len(checks), 4)

    def _focus_dimension_hit_rate(self, content: ReportArtifactContent, focus_dimensions: list[str]) -> float:
        """计算关注维度在产物内容中的覆盖率。"""
        return self._keyword_hit_rate_from_corpus(self._build_content_corpus(content), focus_dimensions)

    def _key_evidence_hit_rate(self, content: ReportArtifactContent, key_evidence_points: list[str]) -> float:
        """计算关键证据点在报告正文和证据文本中的覆盖率。"""
        evidence_text = '\n'.join(item.text for item in content.evidence)
        corpus = '\n'.join([self._build_content_corpus(content), evidence_text])
        return self._keyword_hit_rate_from_corpus(corpus, key_evidence_points)

    def _build_content_corpus(self, content: ReportArtifactContent) -> str:
        """把文档分析产物拼成统一语料，用于关键词命中计算。"""
        return '\n'.join(
            [
                content.summary,
                content.report_markdown or '',
                *[item.title for item in content.key_findings],
                *[item.summary for item in content.key_findings],
                *[item.title for item in content.risks],
                *[item.description for item in content.risks],
                *content.open_questions,
            ]
        ).lower()

    def _keyword_hit_rate_from_corpus(self, corpus: str, keywords: list[str]) -> float:
        """在给定语料上计算关键词命中率。"""
        if not keywords:
            return 1.0
        hit_count = sum(1 for keyword in keywords if keyword.lower() in corpus)
        return round(hit_count / max(1, len(keywords)), 4)

    def _forbidden_claim_hit_count(self, content: ReportArtifactContent, forbidden_claims: list[str]) -> int:
        """统计产物中命中的禁用结论数量。"""
        if not forbidden_claims:
            return 0
        corpus = '\n'.join(
            [
                content.summary,
                content.report_markdown or '',
                *[item.title for item in content.key_findings],
                *[item.summary for item in content.key_findings],
                *[item.title for item in content.risks],
                *[item.description for item in content.risks],
            ]
        ).lower()
        return sum(1 for claim in forbidden_claims if claim.lower() in corpus)

    def _extract_evidence_coverage(self, task) -> float:
        """提取任务证据覆盖度指标。"""
        return self._extract_evidence_summary(task)['coverage_score']

    def _extract_evidence_summary(self, task) -> dict[str, Any]:
        """从任务记忆中提取最新证据覆盖摘要。"""
        evidence_entries = [entry for entry in task.task_memory_entries if entry.kind == 'evidence']
        if not evidence_entries:
            return {'coverage_score': 0.0, 'missing_aspects': []}
        latest = evidence_entries[-1]
        coverage = latest.payload.get('coverage_score', 0.0)
        missing_aspects = [str(item).strip() for item in latest.payload.get('missing_aspects', []) if str(item).strip()]
        try:
            normalized_coverage = round(float(coverage), 4)
        except (TypeError, ValueError):
            normalized_coverage = 0.0
        return {'coverage_score': normalized_coverage, 'missing_aspects': missing_aspects}

    def _citation_grounding_ratio(self, items: list[Any], evidence_ids: set[str]) -> float:
        """计算结论项中 citation 引用完全落在证据集合内的比例。"""
        if not items:
            return 1.0
        grounded = 0
        for item in items:
            citation_ids = [str(value).strip() for value in getattr(item, 'citation_ids', []) if str(value).strip()]
            if citation_ids and set(citation_ids).issubset(evidence_ids):
                grounded += 1
        return round(grounded / max(1, len(items)), 4)

    def _collect_task_trace_events(self, trace_start: int, task_id: str) -> list[TraceEvent]:
        """从全局 trace 中截取单个任务运行期间产生的事件。"""
        return [
            event
            for event in self.trace.events[trace_start:]
            if str(event.payload.get('task_id') or '').strip() == task_id
        ]

    def _extract_retrieval_trace(self, task_trace: list[TraceEvent]) -> dict[str, Any]:
        """从任务 trace 中提取检索模式、候选量和命中量指标。"""
        retrieval_events = [event.payload for event in task_trace if event.name in {'retrieval', 'retrieval_multi'}]
        if not retrieval_events:
            return {
                'retrieval_mode': None,
                'rerank_mode': None,
                'retrieval_candidate_count': 0,
                'retrieval_selected_count': 0,
            }
        latest = retrieval_events[-1]
        return {
            'retrieval_mode': latest.get('retrieval_mode'),
            'rerank_mode': latest.get('rerank_mode'),
            'retrieval_candidate_count': sum(
                int(event.get(key, 0) or 0)
                for event in retrieval_events
                for key in ('dense_candidates', 'lexical_candidates', 'graph_candidates')
            ),
            'retrieval_selected_count': sum(int(event.get('hits', 0) or 0) for event in retrieval_events),
        }

    def _build_step_trace(self, task_trace: list[TraceEvent]) -> dict[str, int]:
        """统计任务各步骤完成次数。"""
        step_trace: dict[str, int] = {}
        for event in task_trace:
            if event.name != 'task_step_completed':
                continue
            step_name = str(event.payload.get('step') or 'unknown')
            step_trace[step_name] = step_trace.get(step_name, 0) + 1
        return step_trace

    def _build_tool_trace(self, task) -> dict[str, dict[str, float]]:
        """按工具聚合调用次数、错误率和平均耗时。"""
        tool_trace: dict[str, dict[str, float]] = {}
        for record in task.tool_call_history:
            entry = tool_trace.setdefault(
                record.tool_name,
                {'call_count': 0.0, 'error_count': 0.0, 'total_duration_ms': 0.0},
            )
            entry['call_count'] += 1.0
            entry['error_count'] += 1.0 if record.status == 'error' else 0.0
            entry['total_duration_ms'] += float(record.duration_ms)
        return {
            tool_name: {
                'call_count': round(values['call_count'], 4),
                'error_rate': round(values['error_count'] / values['call_count'], 4) if values['call_count'] else 0.0,
                'avg_duration_ms': (
                    round(values['total_duration_ms'] / values['call_count'], 4) if values['call_count'] else 0.0
                ),
            }
            for tool_name, values in tool_trace.items()
        }

    def _build_artifact_trace(self, task) -> dict[str, float | bool]:
        """汇总任务产物版本、草稿数和评审状态。"""
        final_artifact = task.final_artifact
        draft_count = sum(1 for entry in task.artifact_memory_entries if entry.status == 'draft')
        final_count = sum(1 for entry in task.artifact_memory_entries if entry.status == 'final')
        if final_artifact is not None and final_count == 0:
            final_count = 1
        return {
            'artifact_version_count': float(len(task.artifact_ids)),
            'artifact_memory_count': float(len(task.artifact_memory_entries)),
            'draft_artifact_count': float(draft_count),
            'final_artifact_count': float(final_count),
            'review_passed': final_artifact.review.passed if final_artifact is not None and final_artifact.review else True,
        }

    def _build_sub_agent_trace(self, task) -> dict[str, dict[str, float | int | dict[str, int]]]:
        """按子 Agent 聚合运行次数、失败数和所选工具。"""
        sub_agent_trace: dict[str, DocumentAnalysisSubAgentTraceEntry] = {}
        for record in getattr(task, 'sub_agent_runs', []) or []:
            entry = sub_agent_trace.get(record.agent_name)
            if entry is None:
                entry = cast(
                    DocumentAnalysisSubAgentTraceEntry,
                    {
                        'run_count': 0,
                        'failure_count': 0,
                        'actions': {},
                        'selected_tools': {},
                        'failure_rate': 0.0,
                    },
                )
                sub_agent_trace[record.agent_name] = entry
            entry['run_count'] += 1
            if record.status == 'failed':
                entry['failure_count'] += 1
            actions = dict(entry['actions'])
            actions[record.action] = actions.get(record.action, 0) + 1
            entry['actions'] = actions
            selected_tools = dict(entry['selected_tools'])
            for tool_name in record.selected_tools:
                selected_tools[tool_name] = selected_tools.get(tool_name, 0) + 1
            entry['selected_tools'] = selected_tools
        for agent_name, payload in sub_agent_trace.items():
            run_count = payload['run_count']
            failure_count = payload['failure_count']
            payload['failure_rate'] = round(failure_count / run_count, 4) if run_count else 0.0
        return cast(dict[str, dict[str, Union[float, int, dict[str, int]]]], sub_agent_trace)

    def _summarize_document_analysis_benchmark(
        self,
        samples: list[DocumentAnalysisBenchmarkSample],
    ) -> dict[str, float]:
        """汇总全部 benchmark 样本，生成仪表盘级核心指标。"""
        if not samples:
            return {}
        completed = [item for item in samples if item.status == 'completed']
        latency_values = [item.latency_ms for item in completed]
        return {
            'success_rate': round(len(completed) / len(samples), 4),
            'avg_score': round(mean(item.score for item in samples), 4),
            'avg_findings_hit_rate': round(mean(item.findings_hit_rate for item in samples), 4),
            'avg_risks_hit_rate': round(mean(item.risks_hit_rate for item in samples), 4),
            'avg_focus_dimension_hit_rate': round(mean(item.focus_dimension_hit_rate for item in samples), 4),
            'avg_key_evidence_hit_rate': round(mean(item.key_evidence_hit_rate for item in samples), 4),
            'avg_artifact_completeness': round(mean(item.artifact_completeness for item in samples), 4),
            'avg_evidence_coverage': round(mean(item.evidence_coverage for item in samples), 4),
            'avg_grounded_finding_ratio': round(mean(item.grounded_finding_ratio for item in samples), 4),
            'avg_grounded_risk_ratio': round(mean(item.grounded_risk_ratio for item in samples), 4),
            'avg_evidence_usability_score': round(mean(item.evidence_usability_score for item in samples), 4),
            'unsupported_claim_rate': round(mean(float(item.unsupported_claim_count > 0) for item in samples), 4),
            'forbidden_claim_rate': round(mean(float(item.forbidden_claim_hit_count > 0) for item in samples), 4),
            'review_pass_rate': round(mean(float(item.review_passed) for item in samples), 4),
            'avg_plan_version': round(mean(float(item.plan_version) for item in samples), 4),
            'avg_artifact_versions': round(mean(float(item.artifact_version_count) for item in samples), 4),
            'review_replan_rate': round(mean(float(item.review_replan_count > 0) for item in samples), 4),
            'avg_step_count': round(mean(float(item.step_count) for item in samples), 4),
            'avg_tool_calls': round(mean(float(item.tool_calls) for item in samples), 4),
            'avg_tool_error_count': round(mean(float(item.tool_error_count) for item in samples), 4),
            'avg_sub_agent_run_count': round(mean(float(item.sub_agent_run_count) for item in samples), 4),
            'avg_sub_agent_failure_count': round(mean(float(item.sub_agent_failure_count) for item in samples), 4),
            'avg_retrieval_candidate_count': round(mean(float(item.retrieval_candidate_count) for item in samples), 4),
            'avg_retrieval_selected_count': round(mean(float(item.retrieval_selected_count) for item in samples), 4),
            'avg_evidence_gap_count': round(mean(float(item.evidence_gap_count) for item in samples), 4),
            'avg_review_note_count': round(mean(float(item.review_note_count) for item in samples), 4),
            'avg_estimated_cost_units': round(mean(float(item.estimated_cost_units) for item in samples), 4),
            'p95_latency_ms': _percentile_float(latency_values, 0.95),
        }

    def _build_document_analysis_dashboard_summary(
        self,
        payload: DocumentAnalysisBenchmarkResponse,
    ) -> DocumentAnalysisDashboardSummary:
        """构建文档分析 benchmark 仪表盘摘要。"""
        samples = payload.samples
        if not samples:
            return DocumentAnalysisDashboardSummary(
                benchmark_id=payload.benchmark_id,
                collection_name=payload.collection_name,
                sample_count=payload.sample_count,
                success_count=payload.success_count,
                failed_count=payload.failed_count,
            )
        step_breakdown = self._build_task_step_breakdown(samples)
        tool_breakdown = self._build_task_tool_breakdown(payload.samples)
        sub_agent_breakdown = self._build_task_sub_agent_breakdown(payload.samples)
        retrieval_mode_breakdown = self._build_mode_breakdown(samples, 'retrieval_mode')
        rerank_mode_breakdown = self._build_mode_breakdown(samples, 'rerank_mode')
        artifact_status_breakdown = self._build_artifact_status_breakdown(samples)
        bucket_breakdown = self._build_dashboard_slice_breakdown(samples, 'bucket')
        collection_breakdown = self._build_dashboard_slice_breakdown(samples, 'collection_name')
        worst_samples = self._build_dashboard_worst_samples(samples)
        total_estimated_cost_units = round(sum(float(item.estimated_cost_units) for item in samples), 4)
        return DocumentAnalysisDashboardSummary(
            benchmark_id=payload.benchmark_id,
            collection_name=payload.collection_name,
            sample_count=payload.sample_count,
            success_count=payload.success_count,
            failed_count=payload.failed_count,
            success_rate=payload.metrics.get('success_rate', 0.0),
            avg_score=payload.metrics.get('avg_score', 0.0),
            avg_latency_ms=round(mean(float(item.latency_ms) for item in samples), 4),
            avg_tool_calls=round(mean(float(item.tool_calls) for item in samples), 4),
            avg_step_count=round(mean(float(item.step_count) for item in samples), 4),
            avg_evidence_count=round(mean(float(item.evidence_count) for item in samples), 4),
            avg_open_question_count=round(mean(float(item.open_question_count) for item in samples), 4),
            avg_sub_agent_run_count=payload.metrics.get('avg_sub_agent_run_count', 0.0),
            avg_sub_agent_failure_count=payload.metrics.get('avg_sub_agent_failure_count', 0.0),
            avg_focus_dimension_hit_rate=payload.metrics.get('avg_focus_dimension_hit_rate', 0.0),
            avg_key_evidence_hit_rate=payload.metrics.get('avg_key_evidence_hit_rate', 0.0),
            avg_evidence_coverage=payload.metrics.get('avg_evidence_coverage', 0.0),
            avg_grounded_finding_ratio=payload.metrics.get('avg_grounded_finding_ratio', 0.0),
            avg_grounded_risk_ratio=payload.metrics.get('avg_grounded_risk_ratio', 0.0),
            avg_evidence_usability_score=payload.metrics.get('avg_evidence_usability_score', 0.0),
            unsupported_claim_rate=payload.metrics.get('unsupported_claim_rate', 0.0),
            review_pass_rate=payload.metrics.get('review_pass_rate', 0.0),
            review_replan_rate=payload.metrics.get('review_replan_rate', 0.0),
            avg_plan_version=payload.metrics.get('avg_plan_version', 0.0),
            avg_artifact_versions=payload.metrics.get('avg_artifact_versions', 0.0),
            avg_tool_error_count=payload.metrics.get('avg_tool_error_count', 0.0),
            avg_retrieval_candidate_count=payload.metrics.get('avg_retrieval_candidate_count', 0.0),
            avg_retrieval_selected_count=payload.metrics.get('avg_retrieval_selected_count', 0.0),
            avg_evidence_gap_count=payload.metrics.get('avg_evidence_gap_count', 0.0),
            avg_review_note_count=payload.metrics.get('avg_review_note_count', 0.0),
            total_estimated_cost_units=total_estimated_cost_units,
            avg_estimated_cost_units=payload.metrics.get('avg_estimated_cost_units', 0.0),
            step_breakdown=step_breakdown,
            tool_breakdown=tool_breakdown,
            sub_agent_breakdown=sub_agent_breakdown,
            retrieval_mode_breakdown=retrieval_mode_breakdown,
            rerank_mode_breakdown=rerank_mode_breakdown,
            artifact_status_breakdown=artifact_status_breakdown,
            bucket_breakdown=bucket_breakdown,
            collection_breakdown=collection_breakdown,
            worst_samples=worst_samples,
        )

    def _build_document_analysis_benchmark_gate(
        self,
        request: DocumentAnalysisBenchmarkRequest,
        payload: DocumentAnalysisBenchmarkResponse,
    ) -> DocumentAnalysisBenchmarkGate:
        """根据 benchmark 聚合指标生成门禁判断结果。"""
        metrics = payload.metrics
        reasons: list[str] = []
        status = 'pass'
        if metrics.get('success_rate', 0.0) < request.min_success_rate:
            status = 'fail'
            reasons.append(
                f"success_rate={metrics.get('success_rate', 0.0):.4f} 低于阈值 {request.min_success_rate:.4f}"
            )
        if metrics.get('avg_score', 0.0) < request.min_avg_score:
            status = 'fail'
            reasons.append(
                f"avg_score={metrics.get('avg_score', 0.0):.4f} 低于阈值 {request.min_avg_score:.4f}"
            )
        if metrics.get('unsupported_claim_rate', 0.0) > request.max_unsupported_claim_rate:
            status = 'fail'
            reasons.append(
                f"unsupported_claim_rate={metrics.get('unsupported_claim_rate', 0.0):.4f} 超过阈值 {request.max_unsupported_claim_rate:.4f}"
            )
        if metrics.get('review_replan_rate', 0.0) > request.max_review_replan_rate:
            if status != 'fail':
                status = 'warn'
            reasons.append(
                f"review_replan_rate={metrics.get('review_replan_rate', 0.0):.4f} 超过阈值 {request.max_review_replan_rate:.4f}"
            )
        if metrics.get('p95_latency_ms', 0.0) > request.max_p95_latency_ms:
            if status != 'fail':
                status = 'warn'
            reasons.append(
                f"p95_latency_ms={metrics.get('p95_latency_ms', 0.0):.2f} 超过阈值 {request.max_p95_latency_ms:.2f}"
            )
        if metrics.get('forbidden_claim_rate', 0.0) > 0:
            status = 'fail'
            reasons.append('存在 forbidden_claim 命中，需先处理潜在幻觉结论')
        if not reasons:
            reasons.append('关键任务指标满足门禁阈值，可继续推进。')
        recommendation = '建议通过门禁并继续观察趋势。'
        if status == 'warn':
            recommendation = '建议先灰度并继续观察 review replan 与延迟波动。'
        elif status == 'fail':
            recommendation = '建议先修复任务质量或稳定性问题，再重新执行 benchmark。'
        return DocumentAnalysisBenchmarkGate(
            status=status,
            recommendation=recommendation,
            reasons=reasons,
            thresholds={
                'min_success_rate': request.min_success_rate,
                'min_avg_score': request.min_avg_score,
                'max_unsupported_claim_rate': request.max_unsupported_claim_rate,
                'max_review_replan_rate': request.max_review_replan_rate,
                'max_p95_latency_ms': request.max_p95_latency_ms,
            },
        )

    def _build_task_step_breakdown(self, samples: list[DocumentAnalysisBenchmarkSample]) -> dict[str, float]:
        """按样本聚合任务步骤执行次数与重规划指标。"""
        if not samples:
            return {}
        per_step_counts: dict[str, float] = {}
        for sample in samples:
            for step_name, count in sample.step_trace.items():
                per_step_counts[step_name] = per_step_counts.get(step_name, 0.0) + float(count)
        return {
            'avg_step_count': round(mean(float(item.step_count) for item in samples), 4),
            'avg_review_replan_count': round(mean(float(item.review_replan_count) for item in samples), 4),
            'avg_plan_version': round(mean(float(item.plan_version) for item in samples), 4),
            **{
                f'step::{step_name}': round(total / max(len(samples), 1), 4)
                for step_name, total in sorted(per_step_counts.items())
            },
        }

    def _build_task_tool_breakdown(self, samples: list[DocumentAnalysisBenchmarkSample]) -> dict[str, dict[str, float]]:
        """按工具聚合 benchmark 样本中的调用量、错误率和耗时。"""
        task_ids = [sample.task_id for sample in samples if sample.task_id]
        if self.task_service is None or not task_ids:
            return {}
        tool_counts: dict[str, list[float]] = {}
        tool_errors: dict[str, list[float]] = {}
        tool_duration: dict[str, list[float]] = {}
        for task_id in task_ids:
            task = self.task_service.get_task(task_id)
            for record in task.tool_call_history:
                tool_counts.setdefault(record.tool_name, []).append(1.0)
                tool_errors.setdefault(record.tool_name, []).append(1.0 if record.status == 'error' else 0.0)
                tool_duration.setdefault(record.tool_name, []).append(float(record.duration_ms))
        result: dict[str, dict[str, float]] = {}
        for tool_name, counts in tool_counts.items():
            errors = tool_errors.get(tool_name, [])
            durations = tool_duration.get(tool_name, [])
            result[tool_name] = {
                'call_count': round(sum(counts), 4),
                'avg_calls_per_task': round(sum(counts) / max(len(task_ids), 1), 4),
                'error_rate': round(sum(errors) / len(errors), 4) if errors else 0.0,
                'avg_duration_ms': round(sum(durations) / len(durations), 4) if durations else 0.0,
            }
        return result

    def _build_mode_breakdown(
        self,
        samples: list[DocumentAnalysisBenchmarkSample],
        field_name: str,
    ) -> dict[str, int]:
        """统计指定模式字段在样本中的分布情况。"""
        breakdown: dict[str, int] = {}
        for sample in samples:
            value = str(getattr(sample, field_name) or '').strip()
            if not value:
                continue
            breakdown[value] = breakdown.get(value, 0) + 1
        return breakdown

    def _build_artifact_status_breakdown(self, samples: list[DocumentAnalysisBenchmarkSample]) -> dict[str, float]:
        """聚合产物版本与草稿/最终产物数量指标。"""
        if not samples:
            return {}
        return {
            'avg_artifact_version_count': round(mean(float(item.artifact_version_count) for item in samples), 4),
            'avg_draft_artifact_count': round(
                mean(float(item.artifact_trace.get('draft_artifact_count', 0.0)) for item in samples),
                4,
            ),
            'avg_final_artifact_count': round(
                mean(float(item.artifact_trace.get('final_artifact_count', 0.0)) for item in samples),
                4,
            ),
        }

    def _build_dashboard_slice_breakdown(
        self,
        samples: list[DocumentAnalysisBenchmarkSample],
        field_name: str,
    ) -> dict[str, DocumentAnalysisDashboardSliceSummary]:
        """按指定维度切片聚合仪表盘指标。"""
        grouped: dict[str, list[DocumentAnalysisBenchmarkSample]] = {}
        for sample in samples:
            label = str(getattr(sample, field_name) or '').strip() or 'unknown'
            grouped.setdefault(label, []).append(sample)
        result: dict[str, DocumentAnalysisDashboardSliceSummary] = {}
        for label, items in sorted(grouped.items()):
            completed = [item for item in items if item.status == 'completed']
            result[label] = DocumentAnalysisDashboardSliceSummary(
                label=label,
                sample_count=len(items),
                success_rate=round(len(completed) / max(len(items), 1), 4),
                avg_score=round(mean(float(item.score) for item in items), 4),
                avg_evidence_coverage=round(mean(float(item.evidence_coverage) for item in items), 4),
                avg_evidence_usability_score=round(mean(float(item.evidence_usability_score) for item in items), 4),
                avg_latency_ms=round(mean(float(item.latency_ms) for item in items), 4),
                avg_tool_error_count=round(mean(float(item.tool_error_count) for item in items), 4),
            )
        return result

    def _build_task_sub_agent_breakdown(
        self,
        samples: list[DocumentAnalysisBenchmarkSample],
    ) -> dict[str, dict[str, float | int | dict[str, int]]]:
        """按子 Agent 聚合 benchmark 级运行分布。"""
        if not samples:
            return {}
        run_count_by_agent: dict[str, list[float]] = {}
        failure_count_by_agent: dict[str, list[float]] = {}
        action_breakdown: dict[str, dict[str, int]] = {}
        selected_tool_breakdown: dict[str, dict[str, int]] = {}
        for sample in samples:
            for agent_name, raw_payload in sample.sub_agent_trace.items():
                payload = cast(DocumentAnalysisSubAgentTraceEntry, raw_payload)
                run_count_by_agent.setdefault(agent_name, []).append(float(payload['run_count']))
                failure_count_by_agent.setdefault(agent_name, []).append(float(payload['failure_count']))
                for action_name, count in payload['actions'].items():
                    action_breakdown.setdefault(agent_name, {})
                    action_breakdown[agent_name][action_name] = action_breakdown[agent_name].get(action_name, 0) + count
                for tool_name, count in payload['selected_tools'].items():
                    selected_tool_breakdown.setdefault(agent_name, {})
                    selected_tool_breakdown[agent_name][tool_name] = (
                        selected_tool_breakdown[agent_name].get(tool_name, 0) + count
                    )
        result: dict[str, DocumentAnalysisSubAgentBreakdown] = {}
        for agent_name, counts in run_count_by_agent.items():
            failures = failure_count_by_agent.get(agent_name, [])
            avg_run_count = round(sum(counts) / max(len(samples), 1), 4)
            avg_failure_count = round(sum(failures) / max(len(samples), 1), 4) if failures else 0.0
            result[agent_name] = {
                'run_count': int(round(sum(counts), 0)),
                'avg_runs_per_sample': avg_run_count,
                'failure_count': int(round(sum(failures), 0)) if failures else 0,
                'failure_rate': round(sum(failures) / max(sum(counts), 1.0), 4) if counts else 0.0,
                'avg_failure_count_per_sample': avg_failure_count,
                'actions': dict(sorted(action_breakdown.get(agent_name, {}).items())),
                'selected_tools': dict(sorted(selected_tool_breakdown.get(agent_name, {}).items())),
            }
        return cast(dict[str, dict[str, Union[float, int, dict[str, int]]]], result)

    def _build_dashboard_worst_samples(
        self,
        samples: list[DocumentAnalysisBenchmarkSample],
        limit: int = 5,
    ) -> list[DocumentAnalysisDashboardWorstSample]:
        """按质量风险排序，返回最需要关注的低分样本。"""
        ranked = sorted(
            samples,
            key=lambda item: (
                0 if item.status != 'completed' else 1,
                float(item.score),
                float(item.evidence_usability_score),
                -float(item.unsupported_claim_count),
                -float(item.review_replan_count),
            ),
        )
        return [
            DocumentAnalysisDashboardWorstSample(
                index=item.index,
                task_id=item.task_id,
                bucket=item.bucket,
                collection_name=item.collection_name,
                status=item.status,
                score=item.score,
                evidence_coverage=item.evidence_coverage,
                evidence_usability_score=item.evidence_usability_score,
                unsupported_claim_count=item.unsupported_claim_count,
                review_replan_count=item.review_replan_count,
                error=item.error,
            )
            for item in ranked[:limit]
        ]

    def _build_document_analysis_trend_gate_history(
        self,
        reports: list[dict[str, Any]],
    ) -> list[DocumentAnalysisTrendGateItem]:
        """提取历史报告中的门禁状态时间线。"""
        rows: list[DocumentAnalysisTrendGateItem] = []
        for item in reports:
            result: DocumentAnalysisBenchmarkResponse = item['result']
            gate: DocumentAnalysisBenchmarkGate | None = item['gate']
            rows.append(
                DocumentAnalysisTrendGateItem(
                    completed_at=item['completed_at'],
                    benchmark_id=result.benchmark_id,
                    gate_status=gate.status if gate is not None else 'unknown',
                    recommendation=gate.recommendation if gate is not None else '-',
                )
            )
        return rows

    def _build_document_analysis_metric_trends(
        self,
        reports: list[dict[str, Any]],
    ) -> list[DocumentAnalysisTrendMetricItem]:
        """比较趋势窗口内关键 benchmark 指标的首尾变化。"""
        tracked_metrics = [
            'success_rate',
            'avg_score',
            'avg_evidence_coverage',
            'avg_evidence_usability_score',
            'unsupported_claim_rate',
            'review_replan_rate',
            'avg_sub_agent_run_count',
            'avg_estimated_cost_units',
        ]
        rows: list[DocumentAnalysisTrendMetricItem] = []
        for metric_name in tracked_metrics:
            values = [
                float(getattr(item['dashboard_summary'], metric_name, 0.0) if item['dashboard_summary'] is not None else 0.0)
                for item in reports
            ]
            rows.append(
                DocumentAnalysisTrendMetricItem(
                    metric=metric_name,
                    first_value=values[0],
                    latest_value=values[-1],
                    delta=round(values[-1] - values[0], 4),
                )
            )
        return rows

    def _build_document_analysis_tool_trends(
        self,
        reports: list[dict[str, Any]],
    ) -> list[DocumentAnalysisTrendToolItem]:
        """提取最新一次 dashboard 中的工具表现趋势。"""
        latest_dashboard: DocumentAnalysisDashboardSummary | None = reports[-1]['dashboard_summary']
        latest_tools = latest_dashboard.tool_breakdown if latest_dashboard is not None else {}
        rows: list[DocumentAnalysisTrendToolItem] = []
        for tool_name, payload in sorted(latest_tools.items()):
            rows.append(
                DocumentAnalysisTrendToolItem(
                    tool_name=tool_name,
                    latest_error_rate=float(payload.get('error_rate', 0.0) or 0.0),
                    latest_avg_duration_ms=float(payload.get('avg_duration_ms', 0.0) or 0.0),
                )
            )
        return rows

    def _build_document_analysis_sub_agent_trends(
        self,
        reports: list[dict[str, Any]],
    ) -> list[DocumentAnalysisTrendSubAgentItem]:
        """提取最新一次 dashboard 中的子 Agent 表现趋势。"""
        latest_dashboard: DocumentAnalysisDashboardSummary | None = reports[-1]['dashboard_summary']
        latest_sub_agents = latest_dashboard.sub_agent_breakdown if latest_dashboard is not None else {}
        rows: list[DocumentAnalysisTrendSubAgentItem] = []
        for agent_name, raw_payload in sorted(latest_sub_agents.items()):
            payload = cast(DocumentAnalysisSubAgentBreakdown, raw_payload)
            rows.append(
                DocumentAnalysisTrendSubAgentItem(
                    agent_name=agent_name,
                    latest_run_count=float(payload['avg_runs_per_sample']),
                    latest_failure_rate=float(payload['failure_rate']),
                )
            )
        return rows

    def _build_document_analysis_trend_insights(self, payload: dict[str, Any]) -> list[str]:
        """根据趋势载荷生成面向人的文字洞察摘要。"""
        insights: list[str] = []
        gate_history = payload.get('gate_history') or []
        metric_trends = payload.get('metric_trends') or []
        latest_gate = gate_history[-1].gate_status if gate_history else 'unknown'
        if latest_gate == 'fail':
            insights.append('最近一次任务 benchmark 门禁为 `fail`，当前不建议继续放量。')
        elif latest_gate == 'warn':
            insights.append('最近一次任务 benchmark 门禁为 `warn`，建议继续灰度观察。')
        elif latest_gate == 'pass':
            insights.append('最近一次任务 benchmark 门禁为 `pass`，当前结果可继续作为候选。')
        strongest = max(metric_trends, key=lambda item: abs(float(item.delta)), default=None)
        if strongest is not None:
            sign = '+' if strongest.delta >= 0 else ''
            insights.append(f'波动最大的任务指标是 `{strongest.metric}`，窗口变化 `{sign}{strongest.delta:.4f}`。')
        if not insights:
            insights.append('当前趋势窗口没有发现明显异常。')
        return insights

    def _estimate_task_cost_units(self, task) -> float:
        """按工具调用、证据量和版本数粗估任务成本单位。"""
        tool_call_cost = len(task.tool_call_history) * 1.0
        evidence_cost = max(len(task.final_artifact.content.evidence) if task.final_artifact is not None else 0, 0) * 0.2
        revision_cost = len(task.plan_revisions) * 0.5
        artifact_cost = len(task.artifact_ids) * 0.3
        return round(tool_call_cost + evidence_cost + revision_cost + artifact_cost, 4)


def _normalize_document_analysis_output_format(value: Any) -> DocumentAnalysisOutputFormat:
    """把外部输入的输出格式规范化为受支持枚举值。"""
    raw_value = str(value or 'markdown+json').strip().lower()
    if raw_value in {'markdown', 'json', 'markdown+json'}:
        return cast(DocumentAnalysisOutputFormat, raw_value)
    raise ValueError(f'unsupported output_format: {raw_value}')


def _percentile_float(values: list[int], percentile: float) -> float:
    """计算整数序列在指定分位点上的近似值。"""
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 4)

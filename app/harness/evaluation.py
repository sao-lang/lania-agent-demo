"""Continuous Evaluation Harness 实现。

负责在任务自然完成时沉淀一份在线 scorecard，并与 task / benchmark / report / version 基线做统一回归对比。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.memory import TaskMemory
from app.core.config import Settings
from app.harness.policy import PolicyEngine, PolicyProfile
from app.models.eval import (
    DocumentAnalysisBaselineCandidate,
    DocumentAnalysisBaselineRegistryItem,
    DocumentAnalysisBaselineRegistryResponse,
    DocumentAnalysisBaselineResolutionResponse,
    DocumentAnalysisBenchmarkResponse,
    DocumentAnalysisDashboardSummary,
)
from app.models.task import TaskDetail, TaskEvaluationScorecard, TaskRegressionResult
from app.rag.observability import TraceRecorder


@dataclass(frozen=True)
class RegressionBaseline:
    """描述一个可用于回归比较的基线对象。"""

    kind: str
    reference: str
    scorecard: TaskEvaluationScorecard
    task_id: str | None = None
    collection_name: str | None = None


class EvaluationHarness:
    """为任务完成时生成连续评测结果。"""

    def __init__(
        self,
        memory: TaskMemory,
        trace: TraceRecorder,
        settings: Settings,
        policy_engine: PolicyEngine,
    ) -> None:
        """初始化连续评测所需的依赖。"""

        self.memory = memory
        self.trace = trace
        self.settings = settings
        self.policy_engine = policy_engine

    def evaluate_task(self, task: TaskDetail) -> tuple[TaskEvaluationScorecard, TaskRegressionResult]:
        """生成单次任务的在线 scorecard 与 regression compare。"""
        final_artifact = task.final_artifact
        profile = self.policy_engine.resolve_profile(task.request)
        generated_at = datetime.now(timezone.utc)
        if final_artifact is None:
            scorecard = TaskEvaluationScorecard(
                task_id=task.task_id,
                policy_name=profile.name,
                policy_version=profile.version,
                scorecard_version=profile.version,
                task_success_rate=0.0,
                generated_at=generated_at,
            )
            regression = TaskRegressionResult(status='none', baseline_kind='none', compared_at=generated_at)
            return scorecard, regression
        content = final_artifact.content
        review = final_artifact.review
        artifact_completeness = self._artifact_completeness(content)
        grounding_score = self._grounding_score(content)
        coverage_score = self._coverage_score(task)
        review_score = self._review_score(review)
        execution_metrics = self._execution_metrics(task)
        runtime_metadata = self._build_runtime_metadata(task)
        unsupported_claim_rate = self._unsupported_claim_rate(content, review)
        avg_cost_per_task = self._estimate_task_cost_units(task)
        overall_score = round(
            artifact_completeness * 0.22
            + grounding_score * 0.22
            + coverage_score * 0.18
            + review_score * 0.18
            + execution_metrics['execution_stability_score'] * 0.10
            + (1.0 - unsupported_claim_rate) * 0.10,
            4,
        )
        baseline = self._select_baseline(task.request, profile, exclude_task_id=task.task_id)
        scorecard = TaskEvaluationScorecard(
            task_id=task.task_id,
            policy_name=profile.name,
            policy_version=profile.version,
            scorecard_version=profile.version,
            artifact_completeness=artifact_completeness,
            grounding_score=grounding_score,
            coverage_score=coverage_score,
            review_score=review_score,
            execution_stability_score=execution_metrics['execution_stability_score'],
            avg_tool_latency_ms=execution_metrics['avg_tool_latency_ms'],
            tool_retry_rate=execution_metrics['tool_retry_rate'],
            tool_failure_rate=execution_metrics['tool_failure_rate'],
            tool_fallback_rate=execution_metrics['tool_fallback_rate'],
            unsupported_claim_rate=unsupported_claim_rate,
            task_success_rate=1.0 if task.status == 'completed' else 0.0,
            avg_cost_per_task=avg_cost_per_task,
            overall_score=overall_score,
            regression_baseline=baseline.reference if baseline is not None else None,
            baseline_kind=baseline.kind if baseline is not None else 'none',
            baseline_reference=baseline.reference if baseline is not None else None,
            runtime_metadata=runtime_metadata,
            generated_at=generated_at,
        )
        regression = self._compare_with_baseline(scorecard, baseline)
        self.trace.record(
            'task_evaluation_scorecard_generated',
            {
                'task_id': task.task_id,
                'policy_name': scorecard.policy_name,
                'policy_version': scorecard.policy_version,
                'scorecard_version': scorecard.scorecard_version,
                'overall_score': scorecard.overall_score,
                'artifact_completeness': scorecard.artifact_completeness,
                'grounding_score': scorecard.grounding_score,
                'coverage_score': scorecard.coverage_score,
                'review_score': scorecard.review_score,
                'execution_stability_score': scorecard.execution_stability_score,
                'tool_retry_rate': scorecard.tool_retry_rate,
                'tool_failure_rate': scorecard.tool_failure_rate,
                'tool_fallback_rate': scorecard.tool_fallback_rate,
                'unsupported_claim_rate': scorecard.unsupported_claim_rate,
                'regression_baseline': scorecard.regression_baseline,
                'baseline_kind': scorecard.baseline_kind,
                'regression_status': regression.status,
            },
        )
        return scorecard, regression

    def describe_baseline_resolution(
        self,
        request,
        *,
        exclude_task_id: str | None = None,
    ) -> DocumentAnalysisBaselineResolutionResponse:
        """返回某个任务请求会命中的 baseline 解析结果。"""
        profile = self.policy_engine.resolve_profile(request)
        candidates = self._collect_baseline_candidates(request, profile, exclude_task_id=exclude_task_id)
        selected = self._select_first_available(candidates, profile)
        return DocumentAnalysisBaselineResolutionResponse(
            collection_name=request.collection_name,
            instructions=request.instructions,
            policy_name=profile.name,
            policy_version=profile.version,
            baseline_order=list(profile.evaluation_baseline_order),
            selected_baseline=self._to_candidate_model(selected) if selected is not None else None,
            candidates=[self._to_candidate_model(item) for item in candidates],
        )

    def list_baseline_registry(
        self,
        request,
        *,
        kind: str | None = None,
        limit: int = 20,
        offset: int = 0,
        exclude_task_id: str | None = None,
    ) -> DocumentAnalysisBaselineRegistryResponse:
        """按策略顺序枚举当前请求可见的 baseline 注册表。"""
        profile = self.policy_engine.resolve_profile(request)
        registry_items = self._collect_registry_candidates(request, profile, exclude_task_id=exclude_task_id)
        if kind is not None:
            normalized_kind = kind.strip()
            registry_items = [item for item in registry_items if item.kind == normalized_kind]
        selected = self._select_first_available(registry_items, profile)
        kind_counts: dict[str, int] = {}
        selected_key = (selected.kind, selected.reference) if selected is not None else None
        ordered_items: list[DocumentAnalysisBaselineRegistryItem] = []
        for item in registry_items:
            kind_counts[item.kind] = kind_counts.get(item.kind, 0) + 1
            order_index = self._baseline_order_index(item.kind, profile)
            ordered_items.append(
                DocumentAnalysisBaselineRegistryItem(
                    **self._to_candidate_model(item).model_dump(),
                    selected=selected_key == (item.kind, item.reference),
                    order_index=order_index,
                )
            )
        paged_items = ordered_items[offset: offset + limit]
        return DocumentAnalysisBaselineRegistryResponse(
            collection_name=request.collection_name,
            instructions=request.instructions,
            policy_name=profile.name,
            policy_version=profile.version,
            baseline_order=list(profile.evaluation_baseline_order),
            kind=kind.strip() if kind is not None and kind.strip() else None,
            total=len(ordered_items),
            limit=limit,
            offset=offset,
            kind_counts=kind_counts,
            selected_baseline=self._to_candidate_model(selected) if selected is not None else None,
            items=paged_items,
        )

    def compare_task_runs(self, task: TaskDetail, baseline_task: TaskDetail) -> TaskRegressionResult:
        """对两次任务运行做显式回归对比。"""
        scorecard = task.evaluation_scorecard or self.evaluate_task(task)[0]
        baseline_scorecard = baseline_task.evaluation_scorecard or self.evaluate_task(baseline_task)[0]
        baseline = RegressionBaseline(
            kind='task',
            reference=baseline_task.task_id,
            scorecard=baseline_scorecard,
            task_id=baseline_task.task_id,
            collection_name=baseline_task.request.collection_name,
        )
        return self._compare_with_baseline(scorecard, baseline)

    def summarize_recent_trends(
        self,
        collection_name: str | None = None,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        """聚合最近一段时间的运行趋势，供 dashboard / compare 使用。"""
        tasks = [TaskDetail.model_validate(item) for item in self.memory.state.tasks.values()]
        completed = [
            item
            for item in tasks
            if item.status == 'completed'
            and item.evaluation_scorecard is not None
            and (collection_name is None or item.request.collection_name == collection_name)
        ]
        ordered = sorted(completed, key=lambda item: item.completed_at or item.updated_at, reverse=True)[:limit]
        if not ordered:
            return {
                'collection_name': collection_name,
                'total_runs': 0,
                'avg_overall_score': 0.0,
                'avg_execution_stability_score': 0.0,
                'avg_tool_fallback_rate': 0.0,
                'runs': [],
            }
        scorecards = [item.evaluation_scorecard for item in ordered if item.evaluation_scorecard is not None]
        assert scorecards
        runs = [
            {
                'task_id': item.task_id,
                'completed_at': (item.completed_at or item.updated_at).isoformat(),
                'overall_score': item.evaluation_scorecard.overall_score if item.evaluation_scorecard else 0.0,
                'execution_stability_score': (
                    item.evaluation_scorecard.execution_stability_score if item.evaluation_scorecard else 0.0
                ),
                'tool_fallback_rate': item.evaluation_scorecard.tool_fallback_rate if item.evaluation_scorecard else 0.0,
                'regression_status': item.regression_result.status if item.regression_result is not None else 'none',
            }
            for item in ordered
        ]
        return {
            'collection_name': collection_name or ordered[0].request.collection_name,
            'total_runs': len(ordered),
            'avg_overall_score': round(sum(item.overall_score for item in scorecards) / len(scorecards), 4),
            'avg_execution_stability_score': round(
                sum(item.execution_stability_score for item in scorecards) / len(scorecards),
                4,
            ),
            'avg_tool_fallback_rate': round(sum(item.tool_fallback_rate for item in scorecards) / len(scorecards), 4),
            'runs': runs,
        }

    def _artifact_completeness(self, content) -> float:
        """评估产物关键字段的完整度。"""

        checks = [
            bool(content.summary.strip()),
            bool(content.key_findings),
            bool(content.risks),
            content.report_markdown is not None,
            content.report_json is not None,
            bool(content.evidence),
        ]
        return round(sum(1 for item in checks if item) / len(checks), 4)

    def _grounding_score(self, content) -> float:
        """评估 finding/risk 与证据的绑定比例。"""

        evidence_ids = {item.citation_id for item in content.evidence}
        claim_items = [*content.key_findings, *content.risks]
        if not claim_items:
            return 1.0
        grounded = 0
        for item in claim_items:
            citation_ids = [str(value).strip() for value in getattr(item, 'citation_ids', []) if str(value).strip()]
            if citation_ids and set(citation_ids).issubset(evidence_ids):
                grounded += 1
        return round(grounded / max(1, len(claim_items)), 4)

    def _coverage_score(self, task: TaskDetail) -> float:
        """从任务记忆中读取最近一次证据覆盖度。"""

        evidence_entries = [entry for entry in task.task_memory_entries if entry.kind == 'evidence']
        if not evidence_entries:
            return 0.0
        latest = evidence_entries[-1]
        try:
            return round(float(latest.payload.get('coverage_score', 0.0) or 0.0), 4)
        except (TypeError, ValueError):
            return 0.0

    def _review_score(self, review) -> float:
        """根据 review 结果估算审查质量分数。"""

        if review is None:
            return 1.0
        penalty = min(1.0, len(review.unsupported_claims) * 0.3 + len(review.missing_sections) * 0.2)
        return round(max(0.0, (1.0 if review.passed else 0.6) - penalty), 4)

    def _unsupported_claim_rate(self, content, review) -> float:
        """估算 unsupported claims 在全部 claim 中的占比。"""

        claim_count = len(content.key_findings) + len(content.risks)
        unsupported_count = len(review.unsupported_claims) if review is not None else 0
        if claim_count <= 0:
            return 0.0
        return round(min(1.0, unsupported_count / claim_count), 4)

    def _estimate_task_cost_units(self, task: TaskDetail) -> float:
        """基于工具调用、产物数和重规划次数粗估任务成本。"""

        return round(
            float(task.metrics.tool_calls)
            + float(len(task.artifact_ids)) * 0.3
            + float(len(task.plan_revisions)) * 0.5,
            4,
        )

    def _execution_metrics(self, task: TaskDetail) -> dict[str, float]:
        """汇总执行稳定性、时延、重试与降级指标。"""

        tool_calls = list(task.tool_call_history)
        runtime_entries = [
            entry
            for entry in task.task_memory_entries
            if entry.payload.get('runtime_category') == 'execution'
        ]
        total_calls = len(tool_calls)
        if total_calls <= 0:
            return {
                'execution_stability_score': 1.0,
                'avg_tool_latency_ms': 0.0,
                'tool_retry_rate': 0.0,
                'tool_failure_rate': 0.0,
                'tool_fallback_rate': 0.0,
            }
        failure_count = sum(1 for item in tool_calls if item.status == 'error')
        retry_count = sum(max(0, int(item.retry_count)) for item in tool_calls)
        avg_tool_latency_ms = round(sum(max(0, int(item.duration_ms)) for item in tool_calls) / total_calls, 2)
        fallback_count = sum(1 for entry in runtime_entries if bool(entry.payload.get('used_fallback')))
        tool_failure_rate = round(failure_count / total_calls, 4)
        tool_retry_rate = round(retry_count / total_calls, 4)
        tool_fallback_rate = round(fallback_count / total_calls, 4)
        execution_stability_score = round(
            max(0.0, 1.0 - (tool_failure_rate * 0.6 + tool_fallback_rate * 0.3 + min(tool_retry_rate, 1.0) * 0.1)),
            4,
        )
        return {
            'execution_stability_score': execution_stability_score,
            'avg_tool_latency_ms': avg_tool_latency_ms,
            'tool_retry_rate': tool_retry_rate,
            'tool_failure_rate': tool_failure_rate,
            'tool_fallback_rate': tool_fallback_rate,
        }

    def _build_runtime_metadata(self, task: TaskDetail) -> dict[str, Any]:
        """从任务记忆中提取 prompt、grounding 和执行元数据。"""

        prompt_entries = [
            entry.payload
            for entry in task.task_memory_entries
            if entry.payload.get('runtime_category') == 'prompt'
        ]
        execution_entries = [
            entry.payload
            for entry in task.task_memory_entries
            if entry.payload.get('runtime_category') == 'execution'
        ]
        analysis_payloads = [
            entry.payload
            for entry in task.task_memory_entries
            if entry.kind in {'analysis', 'review', 'state'}
        ]
        prompt_versions = sorted(
            {
                f"{item.get('prompt_template_id')}:{item.get('prompt_version')}"
                for item in prompt_entries
                if item.get('prompt_template_id') and item.get('prompt_version')
            }
        )
        context_steps = sorted({str(item.get('context_step_id')) for item in prompt_entries if item.get('context_step_id')})
        grounding_versions = sorted(
            {
                str(item.get('grounding_runtime_version'))
                for item in analysis_payloads
                if item.get('grounding_runtime_version')
            }
        )
        failure_categories = sorted(
            {
                str(item.get('failure_category'))
                for item in execution_entries
                if item.get('failure_category')
            }
        )
        return {
            'prompt_versions': prompt_versions,
            'context_steps': context_steps,
            'grounding_versions': grounding_versions,
            'prompt_count': len(prompt_entries),
            'execution_entry_count': len(execution_entries),
            'failure_categories': failure_categories,
            'max_prompt_token_count': max((int(item.get('prompt_token_count') or 0) for item in prompt_entries), default=0),
            'max_timeout_budget_ms': max((int(item.get('timeout_budget_ms') or 0) for item in execution_entries), default=0),
        }

    def _baseline_by_kind(
        self,
        kind: str,
        request,
        profile: PolicyProfile,
        *,
        exclude_task_id: str | None = None,
    ) -> RegressionBaseline | None:
        """按基线类型查找单个候选基线。"""

        if kind == 'benchmark':
            return self._find_benchmark_baseline(request.collection_name)
        if kind == 'report':
            return self._find_report_baseline(profile)
        if kind == 'version':
            return self._find_task_baseline(
                request.collection_name,
                exclude_task_id=exclude_task_id,
                scorecard_version=profile.version,
                kind='version',
            )
        if kind == 'task':
            return self._find_task_baseline(
                request.collection_name,
                exclude_task_id=exclude_task_id,
                scorecard_version=None,
                kind='task',
            )
        return None

    def _collect_baseline_candidates(
        self,
        request,
        profile: PolicyProfile,
        *,
        exclude_task_id: str | None = None,
    ) -> list[RegressionBaseline]:
        """按策略顺序收集可用基线候选。"""

        seen: set[tuple[str, str]] = set()
        candidates: list[RegressionBaseline] = []
        for kind in profile.evaluation_baseline_order:
            baseline = self._baseline_by_kind(kind, request, profile, exclude_task_id=exclude_task_id)
            if baseline is None:
                continue
            key = (baseline.kind, baseline.reference)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(baseline)
        return candidates

    def _collect_registry_candidates(
        self,
        request,
        profile: PolicyProfile,
        *,
        exclude_task_id: str | None = None,
    ) -> list[RegressionBaseline]:
        """收集并排序注册表视角下的全部基线候选。"""

        seen: set[tuple[str, str]] = set()
        candidates: list[RegressionBaseline] = []
        for kind in profile.evaluation_baseline_order:
            for baseline in self._baseline_candidates_by_kind(
                kind,
                request,
                profile,
                exclude_task_id=exclude_task_id,
            ):
                key = (baseline.kind, baseline.reference)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(baseline)
        candidates.sort(
            key=lambda item: (
                self._baseline_order_index(item.kind, profile),
                self._baseline_sort_key(item),
            )
        )
        return candidates

    def _select_first_available(self, candidates: list[RegressionBaseline], profile: PolicyProfile) -> RegressionBaseline | None:
        """按策略声明顺序选出第一个可用基线。"""

        candidate_map = {(item.kind, item.reference): item for item in candidates}
        for kind in profile.evaluation_baseline_order:
            for item in candidates:
                if item.kind == kind:
                    return candidate_map[(item.kind, item.reference)]
        return None

    def _select_baseline(self, request, profile: PolicyProfile, *, exclude_task_id: str | None = None) -> RegressionBaseline | None:
        """为当前请求选择最终用于比较的基线。"""

        candidates = self._collect_baseline_candidates(request, profile, exclude_task_id=exclude_task_id)
        return self._select_first_available(candidates, profile)

    def _baseline_candidates_by_kind(
        self,
        kind: str,
        request,
        profile: PolicyProfile,
        *,
        exclude_task_id: str | None = None,
    ) -> list[RegressionBaseline]:
        """按指定类型枚举全部基线候选。"""

        if kind == 'benchmark':
            return self._list_benchmark_baselines(request.collection_name)
        if kind == 'report':
            baseline = self._find_report_baseline(profile)
            return [baseline] if baseline is not None else []
        if kind == 'version':
            return self._list_task_baselines(
                request.collection_name,
                exclude_task_id=exclude_task_id,
                scorecard_version=profile.version,
                kind='version',
            )
        if kind == 'task':
            return self._list_task_baselines(
                request.collection_name,
                exclude_task_id=exclude_task_id,
                scorecard_version=None,
                kind='task',
            )
        return []

    def _find_task_baseline(
        self,
        collection_name: str,
        *,
        exclude_task_id: str | None,
        scorecard_version: str | None,
        kind: str,
    ) -> RegressionBaseline | None:
        """查找与当前集合匹配的最近任务基线。"""

        candidates: list[TaskDetail] = []
        for payload in self.memory.state.tasks.values():
            candidate = TaskDetail.model_validate(payload)
            if exclude_task_id is not None and candidate.task_id == exclude_task_id:
                continue
            if candidate.status != 'completed':
                continue
            if candidate.request.collection_name != collection_name:
                continue
            if candidate.evaluation_scorecard is None:
                continue
            if scorecard_version is not None and candidate.evaluation_scorecard.scorecard_version != scorecard_version:
                continue
            candidates.append(candidate)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.completed_at or item.updated_at, reverse=True)
        baseline_task = candidates[0]
        assert baseline_task.evaluation_scorecard is not None
        return RegressionBaseline(
            kind=kind,
            reference=baseline_task.task_id,
            scorecard=baseline_task.evaluation_scorecard,
            task_id=baseline_task.task_id,
            collection_name=baseline_task.request.collection_name,
        )

    def _find_benchmark_baseline(self, collection_name: str) -> RegressionBaseline | None:
        """查找与集合匹配的 benchmark 基线。"""

        for path in sorted(self.settings.eval_dir.glob('*.json'), reverse=True):
            baseline = self._load_benchmark_baseline(path, collection_name)
            if baseline is not None:
                return baseline
        return None

    def _list_task_baselines(
        self,
        collection_name: str,
        *,
        exclude_task_id: str | None,
        scorecard_version: str | None,
        kind: str,
    ) -> list[RegressionBaseline]:
        """列出与集合匹配的任务基线列表。"""

        candidates: list[TaskDetail] = []
        for payload in self.memory.state.tasks.values():
            candidate = TaskDetail.model_validate(payload)
            if exclude_task_id is not None and candidate.task_id == exclude_task_id:
                continue
            if candidate.status != 'completed':
                continue
            if candidate.request.collection_name != collection_name:
                continue
            if candidate.evaluation_scorecard is None:
                continue
            if scorecard_version is not None and candidate.evaluation_scorecard.scorecard_version != scorecard_version:
                continue
            candidates.append(candidate)
        candidates.sort(key=lambda item: item.completed_at or item.updated_at, reverse=True)
        results: list[RegressionBaseline] = []
        for candidate in candidates:
            assert candidate.evaluation_scorecard is not None
            results.append(
                RegressionBaseline(
                    kind=kind,
                    reference=candidate.task_id,
                    scorecard=candidate.evaluation_scorecard,
                    task_id=candidate.task_id,
                    collection_name=candidate.request.collection_name,
                )
            )
        return results

    def _list_benchmark_baselines(self, collection_name: str) -> list[RegressionBaseline]:
        """列出与集合匹配的全部 benchmark 基线。"""

        baselines: list[RegressionBaseline] = []
        for path in sorted(self.settings.eval_dir.glob('*.json'), reverse=True):
            baseline = self._load_benchmark_baseline(path, collection_name)
            if baseline is not None:
                baselines.append(baseline)
        return baselines

    def _find_report_baseline(self, profile: PolicyProfile) -> RegressionBaseline | None:
        """根据 profile 配置查找 report 基线。"""

        if not profile.evaluation_report_path:
            return None
        path = Path(profile.evaluation_report_path).expanduser()
        if not path.is_absolute():
            path = (self.settings.eval_dir / path).resolve()
        return self._load_benchmark_baseline(path, collection_name=None, as_report=True)

    def _load_benchmark_baseline(
        self,
        path: Path,
        collection_name: str | None,
        *,
        as_report: bool = False,
    ) -> RegressionBaseline | None:
        """从 benchmark 报告文件中加载回归基线。"""

        if not path.exists():
            return None
        try:
            raw_payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        if not isinstance(raw_payload, dict) or raw_payload.get('report_mode') != 'document_analysis_benchmark':
            return None
        result_payload = raw_payload.get('result')
        dashboard_payload = raw_payload.get('dashboard_summary')
        if not isinstance(result_payload, dict):
            return None
        try:
            result = DocumentAnalysisBenchmarkResponse.model_validate(result_payload)
            dashboard = (
                DocumentAnalysisDashboardSummary.model_validate(dashboard_payload)
                if isinstance(dashboard_payload, dict)
                else None
            )
        except Exception:
            return None
        if collection_name and str(result.collection_name or '').strip() != collection_name:
            return None
        benchmark_scorecard = self._benchmark_to_scorecard(result, dashboard)
        kind = 'report' if as_report else 'benchmark'
        reference = str(path) if as_report else result.benchmark_id
        return RegressionBaseline(
            kind=kind,
            reference=reference,
            scorecard=benchmark_scorecard,
            collection_name=result.collection_name,
        )

    def _benchmark_to_scorecard(
        self,
        result: DocumentAnalysisBenchmarkResponse,
        dashboard: DocumentAnalysisDashboardSummary | None,
    ) -> TaskEvaluationScorecard:
        """把 benchmark 结果转换为统一 scorecard。"""

        metrics = result.metrics
        grounded_finding = float(metrics.get('avg_grounded_finding_ratio', 0.0) or 0.0)
        grounded_risk = float(metrics.get('avg_grounded_risk_ratio', 0.0) or 0.0)
        grounding_score = round((grounded_finding + grounded_risk) / 2, 4)
        return TaskEvaluationScorecard(
            task_id=f'benchmark::{result.benchmark_id}',
            policy_name='benchmark',
            policy_version='v1',
            scorecard_version='v1',
            artifact_completeness=float(metrics.get('avg_artifact_completeness', 0.0) or 0.0),
            grounding_score=grounding_score,
            coverage_score=float(metrics.get('avg_evidence_coverage', 0.0) or 0.0),
            review_score=float(metrics.get('review_pass_rate', 0.0) or 0.0),
            unsupported_claim_rate=float(metrics.get('unsupported_claim_rate', 0.0) or 0.0),
            task_success_rate=float(metrics.get('success_rate', 0.0) or 0.0),
            avg_cost_per_task=float(metrics.get('avg_estimated_cost_units', 0.0) or 0.0),
            overall_score=float(metrics.get('avg_score', dashboard.avg_score if dashboard is not None else 0.0) or 0.0),
            generated_at=result.completed_at if isinstance(result.completed_at, datetime) else datetime.now(timezone.utc),
        )

    def _compare_with_baseline(
        self,
        scorecard: TaskEvaluationScorecard,
        baseline: RegressionBaseline | None,
    ) -> TaskRegressionResult:
        """把当前 scorecard 与基线做指标对比。"""

        if baseline is None:
            return TaskRegressionResult(status='none', baseline_kind='none', compared_at=datetime.now(timezone.utc))
        baseline_scorecard = baseline.scorecard
        metric_deltas = {
            'overall_score': round(scorecard.overall_score - baseline_scorecard.overall_score, 4),
            'artifact_completeness': round(scorecard.artifact_completeness - baseline_scorecard.artifact_completeness, 4),
            'grounding_score': round(scorecard.grounding_score - baseline_scorecard.grounding_score, 4),
            'coverage_score': round(scorecard.coverage_score - baseline_scorecard.coverage_score, 4),
            'review_score': round(scorecard.review_score - baseline_scorecard.review_score, 4),
            'execution_stability_score': round(
                scorecard.execution_stability_score - baseline_scorecard.execution_stability_score,
                4,
            ),
            'tool_fallback_rate': round(scorecard.tool_fallback_rate - baseline_scorecard.tool_fallback_rate, 4),
            'tool_failure_rate': round(scorecard.tool_failure_rate - baseline_scorecard.tool_failure_rate, 4),
            'unsupported_claim_rate': round(scorecard.unsupported_claim_rate - baseline_scorecard.unsupported_claim_rate, 4),
        }
        reasons: list[str] = []
        status = 'pass'
        for metric_name in (
            'overall_score',
            'artifact_completeness',
            'grounding_score',
            'coverage_score',
            'review_score',
            'execution_stability_score',
        ):
            if metric_deltas[metric_name] <= -0.15:
                status = 'fail'
                reasons.append(f'{metric_name} 相比基线下降 {abs(metric_deltas[metric_name]):.4f}')
            elif metric_deltas[metric_name] <= -0.05 and status != 'fail':
                status = 'warn'
                reasons.append(f'{metric_name} 相比基线轻微下降 {abs(metric_deltas[metric_name]):.4f}')
        if metric_deltas['tool_fallback_rate'] > 0.1:
            status = 'fail'
            reasons.append(f'tool_fallback_rate 相比基线上升 {metric_deltas["tool_fallback_rate"]:.4f}')
        if metric_deltas['tool_failure_rate'] > 0.05:
            status = 'fail'
            reasons.append(f'tool_failure_rate 相比基线上升 {metric_deltas["tool_failure_rate"]:.4f}')
        if metric_deltas['unsupported_claim_rate'] > 0:
            status = 'fail'
            reasons.append(f'unsupported_claim_rate 相比基线上升 {metric_deltas["unsupported_claim_rate"]:.4f}')
        if not reasons:
            reasons.append('关键任务指标未出现明显退化。')
        return TaskRegressionResult(
            baseline_task_id=baseline.task_id,
            baseline_kind=baseline.kind,
            baseline_reference=baseline.reference,
            status=status,
            reasons=reasons,
            metric_deltas=metric_deltas,
            compared_at=datetime.now(timezone.utc),
        )

    def _to_candidate_model(self, baseline: RegressionBaseline) -> DocumentAnalysisBaselineCandidate:
        """把内部基线对象转换为对外响应模型。"""

        scorecard = baseline.scorecard
        return DocumentAnalysisBaselineCandidate(
            kind=baseline.kind,
            reference=baseline.reference,
            task_id=baseline.task_id,
            collection_name=baseline.collection_name,
            policy_name=scorecard.policy_name,
            policy_version=scorecard.policy_version,
            scorecard_version=scorecard.scorecard_version,
            overall_score=scorecard.overall_score,
            coverage_score=scorecard.coverage_score,
            grounding_score=scorecard.grounding_score,
            review_score=scorecard.review_score,
            unsupported_claim_rate=scorecard.unsupported_claim_rate,
            generated_at=scorecard.generated_at,
        )

    def _baseline_order_index(self, kind: str, profile: PolicyProfile) -> int:
        """返回某类基线在策略顺序中的位置。"""

        try:
            return profile.evaluation_baseline_order.index(kind)
        except ValueError:
            return len(profile.evaluation_baseline_order)

    def _baseline_sort_key(self, baseline: RegressionBaseline) -> tuple[float, str]:
        """生成基线排序键，优先最新结果。"""

        generated_at = baseline.scorecard.generated_at
        timestamp = generated_at.timestamp() if generated_at is not None else 0.0
        return (-timestamp, baseline.reference)

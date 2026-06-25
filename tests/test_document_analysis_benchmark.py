"""文档分析基准评测测试，覆盖评测执行、基线管理、历史趋势与接口响应。"""

import json
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.memory import TaskMemory
from app.core.config import Settings
from app.harness.evaluation import EvaluationHarness
from app.harness.policy import PolicyEngine
from app.main import create_app
from app.models.artifact import Artifact, ReportArtifactContent, EvidenceItem, FindingItem, RiskItem
from app.models.eval import (
    DocumentAnalysisBenchmarkRequest,
    ManagedDocumentAnalysisBaselineRegisterRequest,
    ManagedDocumentAnalysisBaselineUpdateRequest,
)
from app.models.task import (
    PlanRevision,
    SubAgentRunRecord,
    TaskDetail,
    TaskMemoryEntry,
    TaskMetrics,
    TaskRequest,
    ToolCallRecord,
)
from app.rag.observability import TraceRecorder
from app.services.eval_service import EvalService
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class FakeTaskService:
    """测试桩 `FakeTaskService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, trace: TraceRecorder, settings: Settings, state: InMemoryState) -> None:
        self.trace = trace
        self.calls = 0
        self.memory = TaskMemory(state)
        self.policy_engine = PolicyEngine(settings)
        self.runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(
                evaluation_harness=EvaluationHarness(self.memory, trace, settings, self.policy_engine)
            )
        )
        now = datetime.now(timezone.utc)
        self.artifact = Artifact(
            artifact_id='artifact-1',
            task_id='task-1',
            artifact_type='document_analysis_report',
            version=1,
            status='final',
            content=ReportArtifactContent(
                summary='报告覆盖核心模块、接口依赖与风险。',
                key_findings=[
                    FindingItem(finding_id='finding-1', title='核心模块稳定', summary='系统包含核心模块和接口依赖。', citation_ids=['c1'])
                ],
                risks=[
                    RiskItem(risk_id='risk-1', title='异常处理风险', description='异常处理不足属于主要风险。', severity='high', citation_ids=['c2'])
                ],
                evidence=[
                    EvidenceItem(citation_id='c1', source='design.md', chunk_id='chunk-1', text='核心模块与接口依赖说明。'),
                    EvidenceItem(citation_id='c2', source='design.md', chunk_id='chunk-2', text='异常处理不足是风险点。'),
                ],
                open_questions=['容量评估待确认'],
                confidence=0.8,
                report_markdown='# 文档分析报告\n\n包含核心模块、接口依赖与风险。',
                report_json={'summary': 'ok'},
            ),
            created_at=now,
            updated_at=now,
        )
        self.task = TaskDetail(
            task_id='task-1',
            status='completed',
            request=TaskRequest(collection_name='demo', doc_ids=['doc-1'], instructions='总结核心模块、接口依赖、风险点和未决问题'),
            final_artifact_id='artifact-1',
            artifact_ids=['artifact-1'],
            plan_version=2,
            metrics=TaskMetrics(step_count=6, tool_calls=8, latency_ms=100),
            plan_revisions=[
                PlanRevision(version=2, trigger='evidence_gap', reason='容量评估证据不足', added_steps=['s2r'], created_at=now)
            ],
            task_memory_entries=[
                TaskMemoryEntry(
                    entry_id='tm-1',
                    step='retrieve_evidence',
                    kind='evidence',
                    summary='检索证据完成',
                    payload={'coverage_score': 0.75},
                    created_at=now,
                )
            ],
            tool_call_history=[
                ToolCallRecord(
                    tool_call_id='tool-1',
                    tool_name='rag_retrieve_evidence',
                    step='retrieve_evidence',
                    status='ok',
                    duration_ms=12,
                    input_preview={},
                    output_summary={},
                    created_at=now,
                ),
                ToolCallRecord(
                    tool_call_id='tool-2',
                    tool_name='review_report',
                    step='review_artifact',
                    status='error',
                    duration_ms=8,
                    input_preview={},
                    output_summary=None,
                    error='unsupported claim',
                    created_at=now,
                ),
            ],
            sub_agent_runs=[
                SubAgentRunRecord(
                    run_id='sag-1',
                    agent_name='evidence_agent',
                    action='collect_evidence',
                    status='completed',
                    allowed_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
                    selected_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
                    input_summary={'doc_count': 1},
                    output_summary={'coverage_score': 0.75},
                    created_at=now,
                ),
                SubAgentRunRecord(
                    run_id='sag-2',
                    agent_name='review_agent',
                    action='review_artifact',
                    status='completed',
                    allowed_tools=['review_report', 'draft_report'],
                    selected_tools=['review_report'],
                    input_summary={'finding_count': 1},
                    output_summary={'passed': False},
                    created_at=now,
                ),
            ],
            created_at=now,
            updated_at=now,
            final_artifact=self.artifact,
        )

    def create_document_analysis(self, payload: TaskRequest) -> TaskDetail:
        self.calls += 1
        self.task.request = payload
        self.task.task_id = f'task-{self.calls}'
        self.task.final_artifact.task_id = self.task.task_id
        self.trace.record('task_started', {'task_id': self.task.task_id, 'task_type': payload.task_type})
        for step in ['load_task', 'plan_task', 'retrieve_evidence', 'analyze', 'draft_artifact', 'review_artifact', 'finalize']:
            self.trace.record('task_step_completed', {'task_id': self.task.task_id, 'step': step})
        self.trace.record(
            'retrieval',
            {
                'task_id': self.task.task_id,
                'step_name': 'retrieve_evidence',
                'tool_name': 'rag_retrieve_evidence',
                'retrieval_mode': 'hybrid_graph',
                'rerank_mode': 'lexical',
                'dense_candidates': 4,
                'lexical_candidates': 2,
                'graph_candidates': 1,
                'hits': 2,
            },
        )
        self.trace.record(
            'task_sub_agent_started',
            {'task_id': self.task.task_id, 'agent_name': 'evidence_agent', 'action': 'collect'},
        )
        self.trace.record(
            'task_sub_agent_completed',
            {
                'task_id': self.task.task_id,
                'agent_name': 'evidence_agent',
                'action': 'collect_evidence',
                'selected_tools': ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
            },
        )
        self.trace.record(
            'task_sub_agent_started',
            {'task_id': self.task.task_id, 'agent_name': 'review_agent', 'action': 'review'},
        )
        self.trace.record(
            'task_sub_agent_completed',
            {
                'task_id': self.task.task_id,
                'agent_name': 'review_agent',
                'action': 'review_artifact',
                'selected_tools': ['review_report'],
            },
        )
        self.trace.record(
            'task_artifact_stored',
            {
                'task_id': self.task.task_id,
                'artifact_id': self.task.final_artifact_id,
                'artifact_type': 'document_analysis_report',
                'version': 1,
                'status': 'final',
            },
        )
        self.trace.record(
            'task_workflow_finalized',
            {
                'task_id': self.task.task_id,
                'step_count': self.task.metrics.step_count,
                'tool_calls': self.task.metrics.tool_calls,
                'latency_ms': self.task.metrics.latency_ms,
                'sub_agent_runs': len(self.task.sub_agent_runs),
                'sub_agent_failures': 0,
                'plan_version': self.task.plan_version,
                'artifact_count': len(self.task.artifact_ids),
                'task_memory_count': len(self.task.task_memory_entries),
                'artifact_memory_count': len(self.task.artifact_memory_entries),
                'plan_revision_count': len(self.task.plan_revisions),
                'final_review_passed': True,
                'unsupported_claim_count': 0,
            },
        )
        self.trace.record(
            'task_completed',
            {
                'task_id': self.task.task_id,
                'status': 'completed',
                'metrics': self.task.metrics.model_dump(mode='json'),
            },
        )
        return self.task

    def get_task(self, task_id: str) -> TaskDetail:
        return self.task


class DocumentAnalysisBenchmarkTests(unittest.TestCase):
    """文档分析基准测试集合，覆盖基准报告、基线策略和历史趋势相关行为。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.persistence = SQLiteStateStore(self.settings)
        self.dataset_path = Path(tempfile.mkdtemp()) / 'benchmark.json'
        self.dataset_path.write_text(
            json.dumps([
                {
                    'collection_name': 'demo',
                    'doc_ids': ['doc-1'],
                    'instructions': '总结核心模块、接口依赖、风险点和未决问题',
                    'bucket': 'tech_design',
                    'focus_dimensions': ['模块', '风险'],
                    'key_evidence_points': ['核心模块', '异常处理'],
                    'forbidden_claims': ['异常处理不足'],
                    'expected_findings': ['核心模块', '接口依赖'],
                    'expected_risks': ['风险', '异常处理']
                }
            ], ensure_ascii=False),
            encoding='utf-8',
        )
        self.service = EvalService(
            self.settings,
            self.state,
            self.trace,
            query_service=object(),
            task_service=FakeTaskService(self.trace, self.settings, self.state),
            persistence=self.persistence,
        )
        self.task_service = self.service.task_service

    def test_benchmark_document_analysis_returns_metrics_and_result_file(self) -> None:
        """覆盖 `benchmark_document_analysis_returns_metrics_and_result_file` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )

        self.assertEqual(response.sample_count, 1)
        self.assertEqual(response.success_count, 1)
        self.assertGreater(response.metrics['avg_score'], 0)
        self.assertEqual(response.metrics['avg_evidence_coverage'], 0.75)
        self.assertEqual(response.metrics['avg_focus_dimension_hit_rate'], 1.0)
        self.assertEqual(response.metrics['avg_key_evidence_hit_rate'], 1.0)
        self.assertEqual(response.metrics['avg_grounded_finding_ratio'], 1.0)
        self.assertEqual(response.metrics['avg_grounded_risk_ratio'], 1.0)
        self.assertEqual(response.metrics['avg_plan_version'], 2.0)
        self.assertEqual(response.samples[0].plan_version, 2)
        self.assertEqual(response.samples[0].bucket, 'tech_design')
        self.assertEqual(response.samples[0].focus_dimensions, ['模块', '风险'])
        self.assertEqual(response.samples[0].evidence_coverage, 0.75)
        self.assertEqual(response.samples[0].retrieval_mode, 'hybrid_graph')
        self.assertEqual(response.samples[0].rerank_mode, 'lexical')
        self.assertEqual(response.samples[0].retrieval_candidate_count, 7)
        self.assertEqual(response.samples[0].retrieval_selected_count, 2)
        self.assertEqual(response.samples[0].step_trace['retrieve_evidence'], 1)
        self.assertIn('rag_retrieve_evidence', response.samples[0].tool_trace)
        self.assertEqual(response.samples[0].artifact_trace['final_artifact_count'], 1.0)
        self.assertEqual(response.samples[0].tool_error_count, 1)
        self.assertEqual(response.samples[0].sub_agent_run_count, 2)
        self.assertEqual(response.samples[0].sub_agent_failure_count, 0)
        self.assertIn('evidence_agent', response.samples[0].sub_agent_trace)
        self.assertGreater(response.samples[0].estimated_cost_units, 0)
        self.assertIsNotNone(response.dashboard_summary)
        self.assertEqual(response.dashboard_summary.avg_tool_calls, 8.0)
        self.assertEqual(response.dashboard_summary.avg_step_count, 6.0)
        self.assertEqual(response.dashboard_summary.avg_tool_error_count, 1.0)
        self.assertEqual(response.dashboard_summary.avg_sub_agent_run_count, 2.0)
        self.assertEqual(response.dashboard_summary.avg_evidence_usability_score, 0.9125)
        self.assertEqual(response.dashboard_summary.retrieval_mode_breakdown['hybrid_graph'], 1)
        self.assertEqual(response.dashboard_summary.rerank_mode_breakdown['lexical'], 1)
        self.assertIn('review_report', response.dashboard_summary.tool_breakdown)
        self.assertIn('evidence_agent', response.dashboard_summary.sub_agent_breakdown)
        self.assertEqual(response.dashboard_summary.bucket_breakdown['tech_design'].sample_count, 1)
        self.assertEqual(response.dashboard_summary.collection_breakdown['demo'].avg_score, response.dashboard_summary.avg_score)
        self.assertEqual(response.dashboard_summary.worst_samples[0].bucket, 'tech_design')
        self.assertIsNotNone(response.gate)
        self.assertEqual(response.gate.status, 'fail')
        self.assertTrue(Path(response.result_path).exists())
        result_payload = json.loads(Path(response.result_path).read_text(encoding='utf-8'))
        self.assertEqual(result_payload['report_mode'], 'document_analysis_benchmark')
        self.assertEqual(result_payload['gate']['status'], 'fail')
        self.assertEqual(result_payload['dashboard_summary']['avg_evidence_coverage'], 0.75)
        self.assertEqual(result_payload['dashboard_summary']['avg_evidence_usability_score'], 0.9125)
        self.assertEqual(result_payload['dashboard_summary']['avg_sub_agent_run_count'], 2.0)
        self.assertEqual(result_payload['result']['metrics']['avg_plan_version'], 2.0)

    def test_benchmark_endpoint_returns_response(self) -> None:
        """覆盖 `benchmark_endpoint_returns_response` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        app = create_app()
        client = TestClient(app)
        container = app.state.container
        container.eval_service.benchmark_document_analysis = lambda payload: self.service.benchmark_document_analysis(payload)

        response = client.post(
            '/api/v1/eval/tasks/document-analysis/benchmark',
            json={'dataset_path': str(self.dataset_path), 'collection_name': 'demo'},
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body['sample_count'], 1)
        self.assertEqual(body['success_count'], 1)

    def test_benchmark_report_history_detail_and_trend(self) -> None:
        """覆盖 `benchmark_report_history_detail_and_trend` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        assert self.task_service is not None
        self.task_service.task.metrics.latency_ms = 140
        self.task_service.task.metrics.tool_calls = 10
        self.task_service.task.task_memory_entries[0].payload['coverage_score'] = 0.9
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        self.task_service.task.metrics.latency_ms = 90
        self.task_service.task.metrics.tool_calls = 7
        self.task_service.task.task_memory_entries[0].payload['coverage_score'] = 0.6
        third = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo-2')
        )

        history = self.service.list_document_analysis_benchmark_reports(limit=10, offset=0)
        self.assertEqual(history.total, 3)
        self.assertEqual(len(history.items), 3)
        self.assertEqual(history.items[0].benchmark_id, third.benchmark_id)
        self.assertEqual(history.items[1].benchmark_id, second.benchmark_id)
        self.assertEqual(history.items[2].benchmark_id, first.benchmark_id)
        self.assertEqual(history.items[1].avg_evidence_usability_score, second.dashboard_summary.avg_evidence_usability_score)

        filtered_history = self.service.list_document_analysis_benchmark_reports(
            limit=10, offset=0, collection_name='demo', gate_status='fail'
        )
        self.assertEqual(filtered_history.total, 2)
        self.assertTrue(all(item.collection_name == 'demo' for item in filtered_history.items))

        detail = self.service.get_document_analysis_benchmark_report(first.benchmark_id)
        self.assertEqual(detail.result.benchmark_id, first.benchmark_id)
        self.assertEqual(detail.dashboard_summary.avg_evidence_coverage, 0.75)
        self.assertEqual(detail.dashboard_summary.bucket_breakdown['tech_design'].avg_evidence_coverage, 0.75)

        latest = self.service.get_latest_document_analysis_dashboard()
        self.assertEqual(latest.result.benchmark_id, third.benchmark_id)
        self.assertEqual(latest.dashboard_summary.avg_evidence_coverage, 0.6)

        latest_demo = self.service.get_latest_document_analysis_dashboard(collection_name='demo')
        self.assertEqual(latest_demo.result.benchmark_id, second.benchmark_id)
        self.assertEqual(latest_demo.dashboard_summary.avg_evidence_coverage, 0.9)

        trend = self.service.get_document_analysis_trend(limit=10, collection_name='demo')
        self.assertEqual(trend.report_count, 2)
        self.assertEqual(trend.latest_benchmark_id, second.benchmark_id)
        self.assertEqual(trend.latest_dashboard_summary.avg_evidence_coverage, 0.9)
        self.assertEqual(trend.latest_gate.status, 'fail')
        self.assertTrue(trend.sub_agent_trends)
        metric_map = {item.metric: item for item in trend.metric_trends}
        self.assertEqual(metric_map['avg_evidence_coverage'].first_value, 0.75)
        self.assertEqual(metric_map['avg_evidence_coverage'].latest_value, 0.9)
        self.assertEqual(metric_map['avg_evidence_coverage'].delta, 0.15)

    def test_baseline_resolution_returns_selected_benchmark_candidate(self) -> None:
        """覆盖 `baseline_resolution_returns_selected_benchmark_candidate` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        result = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )

        resolution = self.service.get_document_analysis_baseline_resolution(
            collection_name='demo',
            instructions='总结核心模块与风险点',
        )

        self.assertEqual(resolution.collection_name, 'demo')
        self.assertEqual(resolution.policy_name, 'document_analysis_default')
        self.assertIsNotNone(resolution.selected_baseline)
        assert resolution.selected_baseline is not None
        self.assertEqual(resolution.selected_baseline.kind, 'benchmark')
        self.assertEqual(resolution.selected_baseline.reference, result.benchmark_id)
        self.assertTrue(resolution.candidates)

    def test_baseline_registry_returns_selected_items_and_counts(self) -> None:
        """覆盖 `baseline_registry_returns_selected_items_and_counts` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )

        registry = self.service.get_document_analysis_baseline_registry(
            collection_name='demo',
            instructions='总结核心模块与风险点',
            limit=10,
            offset=0,
        )

        self.assertEqual(registry.collection_name, 'demo')
        self.assertEqual(registry.total, 2)
        self.assertEqual(registry.kind_counts['benchmark'], 2)
        self.assertIsNotNone(registry.selected_baseline)
        assert registry.selected_baseline is not None
        self.assertEqual(registry.selected_baseline.reference, second.benchmark_id)
        selected_items = [item for item in registry.items if item.selected]
        self.assertEqual(len(selected_items), 1)
        self.assertEqual(selected_items[0].reference, second.benchmark_id)
        self.assertEqual(registry.items[1].reference, first.benchmark_id)

    def test_managed_baseline_registry_supports_register_update_and_delete(self) -> None:
        """覆盖 `managed_baseline_registry_supports_register_update_and_delete` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        result = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )

        created = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=result.benchmark_id,
                status='active',
                note='首个基线',
            )
        )
        listing = self.service.list_managed_document_analysis_baselines(limit=10, offset=0)

        self.assertEqual(created.reference, result.benchmark_id)
        self.assertEqual(created.status, 'active')
        self.assertEqual(listing.total, 1)
        self.assertEqual(listing.items[0].entry_id, created.entry_id)

        updated = self.service.update_managed_document_analysis_baseline(
            created.entry_id,
            ManagedDocumentAnalysisBaselineUpdateRequest(status='archived', note='已归档'),
        )
        filtered = self.service.list_managed_document_analysis_baselines(status='archived', limit=10, offset=0)

        self.assertEqual(updated.status, 'archived')
        self.assertEqual(updated.note, '已归档')
        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.items[0].entry_id, created.entry_id)

        self.service.delete_managed_document_analysis_baseline(created.entry_id)
        after_delete = self.service.list_managed_document_analysis_baselines(limit=10, offset=0)
        self.assertEqual(after_delete.total, 0)

    def test_managed_baseline_registry_records_audit_log(self) -> None:
        """覆盖 `managed_baseline_registry_records_audit_log` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        result = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        created = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=result.benchmark_id,
                status='active',
                actor='tester',
                actor_role='reviewer',
            )
        )

        self.service.update_managed_document_analysis_baseline(
            created.entry_id,
            ManagedDocumentAnalysisBaselineUpdateRequest(
                status='archived',
                actor='tester',
                actor_role='reviewer',
                review_status='approved',
                review_note='已核验',
            ),
        )
        audits = self.service.list_managed_document_analysis_baseline_audits(entry_id=created.entry_id, limit=10, offset=0)

        self.assertGreaterEqual(audits.total, 2)
        self.assertEqual(audits.items[0].entry_id, created.entry_id)
        self.assertEqual(audits.items[0].actor, 'tester')
        self.assertIn(audits.items[0].action, {'created', 'updated'})

    def test_active_managed_baseline_overrides_auto_selected_candidate(self) -> None:
        """覆盖 `active_managed_baseline_overrides_auto_selected_candidate` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=first.benchmark_id,
                status='active',
                note='锁定旧基线',
            )
        )

        resolution = self.service.get_document_analysis_baseline_resolution(
            collection_name='demo',
            instructions='总结核心模块与风险点',
        )
        registry = self.service.get_document_analysis_baseline_registry(
            collection_name='demo',
            instructions='总结核心模块与风险点',
            limit=10,
            offset=0,
        )

        assert resolution.selected_baseline is not None
        self.assertNotEqual(second.benchmark_id, first.benchmark_id)
        self.assertEqual(resolution.selected_baseline.reference, first.benchmark_id)
        self.assertIsNotNone(resolution.selected_baseline.managed_entry_id)
        self.assertEqual(resolution.selected_baseline.managed_status, 'active')
        selected_items = [item for item in registry.items if item.selected]
        self.assertEqual(len(selected_items), 1)
        self.assertEqual(selected_items[0].reference, first.benchmark_id)
        self.assertEqual(selected_items[0].managed_status, 'active')

    def test_registering_new_active_managed_baseline_archives_previous_active(self) -> None:
        """覆盖 `registering_new_active_managed_baseline_archives_previous_active` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        first_entry = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=first.benchmark_id,
                status='active',
            )
        )
        second_entry = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=second.benchmark_id,
                status='active',
            )
        )

        all_items = self.service.list_managed_document_analysis_baselines(limit=10, offset=0).items
        first_saved = next(item for item in all_items if item.entry_id == first_entry.entry_id)
        second_saved = next(item for item in all_items if item.entry_id == second_entry.entry_id)

        self.assertEqual(first_saved.status, 'archived')
        self.assertEqual(second_saved.status, 'active')
        self.assertEqual(sum(1 for item in all_items if item.collection_name == 'demo' and item.status == 'active'), 1)

    def test_updating_entry_to_active_archives_previous_active(self) -> None:
        """覆盖 `updating_entry_to_active_archives_previous_active` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        first_entry = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=first.benchmark_id,
                status='active',
            )
        )
        second_entry = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=second.benchmark_id,
                status='draft',
            )
        )

        updated_second = self.service.update_managed_document_analysis_baseline(
            second_entry.entry_id,
            ManagedDocumentAnalysisBaselineUpdateRequest(status='active'),
        )
        all_items = self.service.list_managed_document_analysis_baselines(limit=10, offset=0).items
        first_saved = next(item for item in all_items if item.entry_id == first_entry.entry_id)
        second_saved = next(item for item in all_items if item.entry_id == updated_second.entry_id)

        self.assertEqual(first_saved.status, 'archived')
        self.assertEqual(second_saved.status, 'active')
        self.assertEqual(sum(1 for item in all_items if item.collection_name == 'demo' and item.status == 'active'), 1)

    def test_active_managed_baselines_can_coexist_for_different_policies(self) -> None:
        """覆盖 `active_managed_baselines_can_coexist_for_different_policies` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        default_entry = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=first.benchmark_id,
                status='active',
            )
        )
        contract_entry = self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='请做合同风险审查并给出结论',
                kind='benchmark',
                reference=second.benchmark_id,
                status='active',
            )
        )

        all_items = self.service.list_managed_document_analysis_baselines(limit=10, offset=0).items
        default_saved = next(item for item in all_items if item.entry_id == default_entry.entry_id)
        contract_saved = next(item for item in all_items if item.entry_id == contract_entry.entry_id)
        default_resolution = self.service.get_document_analysis_baseline_resolution(
            collection_name='demo',
            instructions='总结核心模块与风险点',
        )
        contract_resolution = self.service.get_document_analysis_baseline_resolution(
            collection_name='demo',
            instructions='请做合同风险审查并给出结论',
        )

        self.assertEqual(default_saved.binding_policy_name, 'document_analysis_default')
        self.assertEqual(contract_saved.binding_policy_name, 'contract_review')
        self.assertEqual(default_saved.status, 'active')
        self.assertEqual(contract_saved.status, 'active')
        self.assertEqual(sum(1 for item in all_items if item.status == 'active'), 2)
        assert default_resolution.selected_baseline is not None
        assert contract_resolution.selected_baseline is not None
        self.assertEqual(default_resolution.selected_baseline.reference, first.benchmark_id)
        self.assertEqual(contract_resolution.selected_baseline.reference, second.benchmark_id)

    def test_instruction_substring_binding_prefers_more_specific_active_baseline(self) -> None:
        """覆盖 `instruction_substring_binding_prefers_more_specific_active_baseline` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        generic = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        specific = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=generic.benchmark_id,
                status='active',
            )
        )
        self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结风险点与合同风险',
                binding_instruction_substring='合同风险',
                kind='benchmark',
                reference=specific.benchmark_id,
                status='active',
            )
        )

        generic_resolution = self.service.get_document_analysis_baseline_resolution(
            collection_name='demo',
            instructions='总结核心模块与风险点',
        )
        specific_resolution = self.service.get_document_analysis_baseline_resolution(
            collection_name='demo',
            instructions='请重点总结合同风险和风险点',
        )

        assert generic_resolution.selected_baseline is not None
        assert specific_resolution.selected_baseline is not None
        self.assertEqual(generic_resolution.selected_baseline.reference, generic.benchmark_id)
        self.assertEqual(specific_resolution.selected_baseline.reference, specific.benchmark_id)

    def test_managed_baseline_listing_supports_binding_filters(self) -> None:
        """覆盖 `managed_baseline_listing_supports_binding_filters` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        first = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='总结核心模块与风险点',
                kind='benchmark',
                reference=first.benchmark_id,
                status='active',
            )
        )
        self.service.register_document_analysis_baseline(
            ManagedDocumentAnalysisBaselineRegisterRequest(
                collection_name='demo',
                instructions='请做合同风险审查并给出结论',
                binding_instruction_substring='合同风险',
                kind='benchmark',
                reference=second.benchmark_id,
                status='active',
            )
        )

        filtered = self.service.list_managed_document_analysis_baselines(
            collection_name='demo',
            binding_policy_name='contract_review',
            binding_instruction_substring='合同风险',
            limit=10,
            offset=0,
        )

        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.items[0].binding_policy_name, 'contract_review')
        self.assertEqual(filtered.items[0].binding_instruction_substring, '合同风险')

    def test_benchmark_history_and_trend_endpoints_return_response(self) -> None:
        """覆盖 `benchmark_history_and_trend_endpoints_return_response` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        result = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        app = create_app()
        client = TestClient(app)
        container = app.state.container
        container.eval_service.list_document_analysis_benchmark_reports = (
            lambda limit=20, offset=0, collection_name=None, gate_status=None: self.service.list_document_analysis_benchmark_reports(
                limit=limit, offset=offset, collection_name=collection_name, gate_status=gate_status
            )
        )
        container.eval_service.get_document_analysis_benchmark_report = (
            lambda benchmark_id: self.service.get_document_analysis_benchmark_report(benchmark_id)
        )
        container.eval_service.get_latest_document_analysis_dashboard = (
            lambda collection_name=None: self.service.get_latest_document_analysis_dashboard(collection_name=collection_name)
        )
        container.eval_service.get_document_analysis_trend = (
            lambda limit=10, collection_name=None: self.service.get_document_analysis_trend(
                limit=limit, collection_name=collection_name
            )
        )
        container.eval_service.get_document_analysis_baseline_resolution = (
            lambda collection_name, instructions='', output_format='markdown': self.service.get_document_analysis_baseline_resolution(
                collection_name=collection_name,
                instructions=instructions,
                output_format=output_format,
            )
        )
        container.eval_service.get_document_analysis_baseline_registry = (
            lambda collection_name, instructions='', output_format='markdown', kind=None, limit=20, offset=0: self.service.get_document_analysis_baseline_registry(
                collection_name=collection_name,
                instructions=instructions,
                output_format=output_format,
                kind=kind,
                limit=limit,
                offset=offset,
            )
        )
        container.eval_service.list_managed_document_analysis_baselines = (
            lambda kind=None, status=None, collection_name=None, binding_policy_name=None, binding_instruction_substring=None, limit=20, offset=0: self.service.list_managed_document_analysis_baselines(
                kind=kind,
                status=status,
                collection_name=collection_name,
                binding_policy_name=binding_policy_name,
                binding_instruction_substring=binding_instruction_substring,
                limit=limit,
                offset=offset,
            )
        )
        container.eval_service.register_document_analysis_baseline = self.service.register_document_analysis_baseline
        container.eval_service.update_managed_document_analysis_baseline = self.service.update_managed_document_analysis_baseline
        container.eval_service.delete_managed_document_analysis_baseline = self.service.delete_managed_document_analysis_baseline

        history_response = client.get('/api/v1/eval/tasks/document-analysis/benchmarks?limit=10&collection_name=demo&gate_status=fail')
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()['total'], 1)

        detail_response = client.get(f'/api/v1/eval/tasks/document-analysis/benchmarks/{result.benchmark_id}')
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()['result']['benchmark_id'], result.benchmark_id)

        dashboard_response = client.get('/api/v1/eval/tasks/document-analysis/dashboard/latest?collection_name=demo')
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(dashboard_response.json()['result']['benchmark_id'], result.benchmark_id)

        trend_response = client.get('/api/v1/eval/tasks/document-analysis/trend?limit=10&collection_name=demo')
        self.assertEqual(trend_response.status_code, 200)
        self.assertEqual(trend_response.json()['report_count'], 1)

        baseline_response = client.get(
            '/api/v1/eval/tasks/document-analysis/baselines/resolve?collection_name=demo&instructions=总结风险点'
        )
        self.assertEqual(baseline_response.status_code, 200)
        baseline_body = baseline_response.json()
        self.assertEqual(baseline_body['policy_name'], 'document_analysis_default')
        self.assertEqual(baseline_body['selected_baseline']['kind'], 'benchmark')

        registry_response = client.get(
            '/api/v1/eval/tasks/document-analysis/baselines?collection_name=demo&instructions=总结风险点&limit=10'
        )
        self.assertEqual(registry_response.status_code, 200)
        registry_body = registry_response.json()
        self.assertEqual(registry_body['policy_name'], 'document_analysis_default')
        self.assertEqual(registry_body['total'], 1)
        self.assertEqual(registry_body['kind_counts']['benchmark'], 1)
        self.assertTrue(registry_body['items'][0]['selected'])

        managed_create_response = client.post(
            '/api/v1/eval/tasks/document-analysis/baselines/managed',
            json={
                'collection_name': 'demo',
                'instructions': '总结风险点',
                'kind': 'benchmark',
                'reference': result.benchmark_id,
                'status': 'active',
                'note': '上线基线',
            },
        )
        self.assertEqual(managed_create_response.status_code, 201)
        managed_entry = managed_create_response.json()
        self.assertEqual(managed_entry['reference'], result.benchmark_id)
        self.assertEqual(managed_entry['status'], 'active')

        managed_list_response = client.get('/api/v1/eval/tasks/document-analysis/baselines/managed?status=active')
        self.assertEqual(managed_list_response.status_code, 200)
        managed_list_body = managed_list_response.json()
        self.assertEqual(managed_list_body['total'], 1)
        self.assertEqual(managed_list_body['items'][0]['entry_id'], managed_entry['entry_id'])

        managed_update_response = client.patch(
            f"/api/v1/eval/tasks/document-analysis/baselines/managed/{managed_entry['entry_id']}",
            json={'status': 'archived', 'note': '已下线'},
        )
        self.assertEqual(managed_update_response.status_code, 200)
        self.assertEqual(managed_update_response.json()['status'], 'archived')

        managed_delete_response = client.delete(
            f"/api/v1/eval/tasks/document-analysis/baselines/managed/{managed_entry['entry_id']}"
        )
        self.assertEqual(managed_delete_response.status_code, 204)

        managed_list_after_delete = client.get('/api/v1/eval/tasks/document-analysis/baselines/managed')
        self.assertEqual(managed_list_after_delete.status_code, 200)
        self.assertEqual(managed_list_after_delete.json()['total'], 0)

        first_for_override = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        second_for_override = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        self.assertNotEqual(first_for_override.benchmark_id, second_for_override.benchmark_id)
        override_create_response = client.post(
            '/api/v1/eval/tasks/document-analysis/baselines/managed',
            json={
                'collection_name': 'demo',
                'instructions': '总结风险点',
                'kind': 'benchmark',
                'reference': first_for_override.benchmark_id,
                'status': 'active',
                'note': '固定旧基线',
            },
        )
        self.assertEqual(override_create_response.status_code, 201)

        override_baseline_response = client.get(
            '/api/v1/eval/tasks/document-analysis/baselines/resolve?collection_name=demo&instructions=总结风险点'
        )
        self.assertEqual(override_baseline_response.status_code, 200)
        override_baseline_body = override_baseline_response.json()
        self.assertEqual(override_baseline_body['selected_baseline']['reference'], first_for_override.benchmark_id)
        self.assertEqual(override_baseline_body['selected_baseline']['managed_status'], 'active')

        contract_baseline = self.service.benchmark_document_analysis(
            DocumentAnalysisBenchmarkRequest(dataset_path=str(self.dataset_path), collection_name='demo')
        )
        contract_create_response = client.post(
            '/api/v1/eval/tasks/document-analysis/baselines/managed',
            json={
                'collection_name': 'demo',
                'instructions': '请做合同风险审查并给出结论',
                'binding_instruction_substring': '合同风险',
                'kind': 'benchmark',
                'reference': contract_baseline.benchmark_id,
                'status': 'active',
            },
        )
        self.assertEqual(contract_create_response.status_code, 201)
        contract_list_response = client.get(
            '/api/v1/eval/tasks/document-analysis/baselines/managed?collection_name=demo&binding_policy_name=contract_review&binding_instruction_substring=%E5%90%88%E5%90%8C%E9%A3%8E%E9%99%A9'
        )
        self.assertEqual(contract_list_response.status_code, 200)
        contract_list_body = contract_list_response.json()
        self.assertEqual(contract_list_body['total'], 1)
        self.assertEqual(contract_list_body['items'][0]['binding_policy_name'], 'contract_review')
        self.assertEqual(contract_list_body['items'][0]['binding_instruction_substring'], '合同风险')


if __name__ == '__main__':
    unittest.main()

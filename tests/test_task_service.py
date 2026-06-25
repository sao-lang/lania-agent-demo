import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.agents.memory import TaskMemory
from app.agents.planner import TaskPlanner
from app.agents.runtime import AgentRuntime
from app.agents.subagents import SubAgentRegistry
from app.agents.tools.analysis_tools import ExtractKeyPointsTool, ExtractRisksTool
from app.agents.tools.artifact_tools import DraftReportTool, FinalizeReportTool, ReviewReportTool
from app.agents.tools.defaults import build_runtime_rag_tools
from app.agents.tools.rag_tools import RagRetrieveEvidenceTool
from app.agents.tools.registry import ToolRegistry
from app.core.config import Settings
from app.core.errors import AppError
from app.models.policy import PolicyProfileCreateRequest, PolicyProfileUpdateRequest
from app.models.query import CitationItem
from app.models.task import TaskPlan, TaskRequest, TaskStep
from app.rag.observability import TraceRecorder
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.services.task_dispatcher import InlineTaskDispatcher
from app.services.task_service import TaskService
from app.workflows.tasks.task_orchestrator import TaskWorkflowOrchestrator


class FakeCollection:
    def get(self, ids, include):
        return {
            'metadatas': [
                {'section_title': '架构设计'},
                {'section_title': '接口依赖'},
                {'section_title': '风险控制'},
            ]
        }


class FakeVectorStore:
    def get_or_create_collection(self, collection_name: str):
        return FakeCollection()


class FakeRetrievalService:
    def retrieve(self, collection_name, question, top_k, **kwargs):
        return [
            CitationItem(
                chunk_id='chunk-1',
                source='design.md',
                text='系统核心模块包括调度、检索和缓存，接口依赖集中在任务服务与工作流之间。',
                score=0.92,
                section_title='架构设计',
                index_kind='hybrid',
                context_scope='chunk',
            ),
            CitationItem(
                chunk_id='chunk-2',
                source='design.md',
                text='当前风险包括异常处理缺失、容量评估不足以及失败重试策略仍需补充。',
                score=0.88,
                section_title='风险控制',
                index_kind='hybrid',
                context_scope='chunk',
            ),
        ][:top_k]


class FailingRetrieveEvidenceTool(RagRetrieveEvidenceTool):
    def run(self, payload, context):
        raise ConnectionError('retrieval dependency unavailable')


class FailingDraftReportTool(DraftReportTool):
    def run(self, payload, context):
        raise OSError('artifact formatter unavailable')


class ImpossibleExitPlanner(TaskPlanner):
    def plan(self, request):
        plan = super().plan(request)
        plan.exit_criteria.append('补齐缺失字段：nonexistent_field')
        return plan


class UnknownToolPlanner(TaskPlanner):
    def plan(self, request):
        return TaskPlan(
            goal='invalid plan',
            expected_artifact='document_analysis_report',
            max_steps=request.constraints.max_steps,
            steps=[
                TaskStep(
                    step_id='s1',
                    intent='调用不存在的工具',
                    tool_name='ghost_tool',
                    required_inputs=['task_request.instructions'],
                    candidate_tools=['ghost_tool'],
                    produced_artifacts=[],
                    failure_branch='abort',
                    success_condition='不会成功',
                )
            ],
            exit_criteria=['报告字段完整'],
        )


class DeferredDispatcher:
    def __init__(self) -> None:
        self.submitted_task_ids: list[str] = []

    def submit(self, task) -> None:
        self.submitted_task_ids.append(task.task_id)

    def shutdown(self) -> None:
        return None


class TaskServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        now = datetime.now(timezone.utc)
        self.state.collections['demo'] = {
            'id': 'col-demo',
            'name': 'demo',
            'description': 'demo collection',
            'status': 'created',
            'embedding_model': 'text-embedding-3-small',
            'chunk_size': 800,
            'chunk_overlap': 100,
            'created_at': now,
            'updated_at': now,
        }
        self.state.documents['doc-1'] = {
            'doc_id': 'doc-1',
            'file_name': 'design.md',
            'file_path': '/tmp/design.md',
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': [],
            'checksum': 'x',
            'status': 'indexed',
            'chunk_ids': ['chunk-1', 'chunk-2'],
            'indexed_chunks': 2,
            'created_at': now,
            'updated_at': now,
            'indexed_at': now,
            'document_title': '系统设计文档',
            'document_summary': '文档介绍了系统模块、接口依赖和风险控制。',
            'document_hierarchy': 'demo / 系统设计文档',
        }
        self.trace = TraceRecorder()
        self.memory = TaskMemory(self.state)
        self.persistence = SQLiteStateStore(self.settings)
        self.registry = ToolRegistry()
        for tool in [
            *build_runtime_rag_tools(),
            ExtractKeyPointsTool(),
            ExtractRisksTool(),
            DraftReportTool(),
            ReviewReportTool(),
            FinalizeReportTool(),
        ]:
            self.registry.register(tool)
        self.orchestrator = TaskWorkflowOrchestrator(
            TaskPlanner(),
            self.registry,
            self.memory,
            self.trace,
            self.settings,
            self.state,
            FakeRetrievalService(),
            FakeVectorStore(),
            None,
        )
        self.runtime = AgentRuntime(self.orchestrator, self.memory, self.trace)
        self.service = TaskService(
            self.runtime,
            self.memory,
            self.state,
            InlineTaskDispatcher(self.runtime),
            self.registry,
            self.orchestrator.subagent_runtime.registry,
            persistence=self.persistence,
        )

    def test_create_document_analysis_generates_final_artifact(self) -> None:
        task = self.service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块、接口依赖、风险点、未决问题和容量评估',
            )
        )

        self.assertEqual(task.status, 'completed')
        self.assertIsNotNone(task.final_artifact_id)
        self.assertIsNotNone(task.final_artifact)
        self.assertGreaterEqual(task.metrics.step_count, 7)
        self.assertGreaterEqual(task.metrics.tool_calls, 6)
        self.assertGreaterEqual(task.metrics.sub_agent_runs, 2)
        self.assertIn('文档分析报告', task.final_artifact.content.report_markdown)
        self.assertGreaterEqual(len(self.service.list_artifacts(task.task_id)), 2)
        self.assertEqual(self.service.list_tasks().total, 1)
        self.assertIsNotNone(task.task_spec)
        self.assertEqual(task.task_spec.task_type, 'document_analysis')
        self.assertTrue(task.task_spec.steps)
        self.assertEqual(task.task_spec.input_payload['skill_name'], 'document_analysis')
        self.assertIsNotNone(task.task_run)
        self.assertEqual(task.task_run.status, 'completed')
        self.assertEqual(task.task_run.budget.max_steps, task.request.constraints.max_steps)
        self.assertTrue(task.task_run.completed_step_ids)
        self.assertEqual(task.task_spec.steps[0].step_id, 'load_task')
        self.assertEqual(task.task_spec.steps[-1].step_id, 'finalize')
        self.assertNotIn('s1', [step.step_id for step in task.task_spec.steps])
        self.assertTrue(set(task.task_run.completed_step_ids).issubset({step.step_id for step in task.task_spec.steps}))
        self.assertGreaterEqual(task.plan_version, 2)
        self.assertTrue(task.task_memory_entries)
        self.assertTrue(task.artifact_memory_entries)
        self.assertTrue(task.reflection_entries)
        self.assertTrue(task.tool_call_history)
        self.assertTrue(task.sub_agent_runs)
        self.assertIsNotNone(task.evaluation_scorecard)
        self.assertIsNotNone(task.regression_result)
        self.assertIn('handle_evidence_gap', task.completed_steps)
        self.assertIn('evaluate_exit_criteria', task.completed_steps)
        self.assertIn('evidence_gap', [item.trigger for item in task.plan_revisions])
        self.assertIn('evidence_gap', [item.trigger for item in task.reflection_entries])
        self.assertIn('review', [item.trigger for item in task.reflection_entries])
        self.assertIn('evidence_agent', [item.agent_name for item in task.sub_agent_runs])
        self.assertIn('reporting_agent', [item.agent_name for item in task.sub_agent_runs])
        self.assertIn('review_agent', [item.agent_name for item in task.sub_agent_runs])
        self.assertTrue(
            any('rag_retrieve_graph_evidence' in item.allowed_tools for item in task.sub_agent_runs if item.agent_name == 'evidence_agent')
        )
        self.assertTrue(
            any('draft_report' in item.allowed_tools for item in task.sub_agent_runs if item.agent_name == 'reporting_agent')
        )
        self.assertTrue(
            any('review_report' in item.allowed_tools for item in task.sub_agent_runs if item.agent_name == 'review_agent')
        )
        self.assertEqual(task.task_memory_entries[-1].step, 'finalize')
        self.assertEqual(task.artifact_memory_entries[-1].status, 'final')
        self.assertTrue(task.plan.steps[0].candidate_tools)
        self.assertTrue(task.plan.steps[0].required_inputs)
        self.assertTrue(any(event.name == 'task_checkpoint_created' for event in task.run_events))

    def test_replay_task_run_uses_checkpoint_on_langgraph_path(self) -> None:
        task = self.service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块、接口依赖和风险点',
            )
        )

        self.assertTrue(task.task_run is not None)
        checkpoint = task.task_run.checkpoints[-1]

        replayed = self.service.replay_task_run(task.task_run.run_id, checkpoint_id=checkpoint.checkpoint_id)

        self.assertNotEqual(replayed.run_id, task.task_run.run_id)
        self.assertEqual(replayed.replayed_from_checkpoint_id, checkpoint.checkpoint_id)
        self.assertEqual(replayed.status, 'completed')

    def test_resume_task_run_uses_latest_checkpoint_on_langgraph_path(self) -> None:
        task = self.service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块、接口依赖和风险点',
            )
        )

        self.assertTrue(task.task_run is not None)
        run_id = task.task_run.run_id
        detail = self.service.get_task_run(run_id)
        run_record = dict(self.state.task_runs[run_id])
        run_record['status'] = 'failed'
        run_record['completed_at'] = None
        run_record['recoverable'] = True
        self.state.task_runs[run_id] = run_record
        self.persistence.upsert_task_run(run_record)

        resumed = self.service.resume_task_run(detail.run_id)

        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(resumed.replayed_from_checkpoint_id, detail.checkpoints[-1].checkpoint_id)

    def test_create_task_supports_document_summary_skill(self) -> None:
        task = self.service.create_task(
            TaskRequest(
                task_type='document_summary',
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='输出文档摘要、关键结论和风险要点',
            )
        )

        self.assertEqual(task.status, 'completed')
        self.assertEqual(task.request.task_type, 'document_summary')
        self.assertIsNotNone(task.task_spec)
        self.assertEqual(task.task_spec.task_type, 'document_summary')
        self.assertEqual(task.task_spec.input_payload['skill_name'], 'document_summary')
        self.assertIn('摘要报告', task.task_spec.objective)
        self.assertTrue(task.task_run is not None)
        self.assertEqual(task.task_run.status, 'completed')
        self.assertIsNotNone(task.final_artifact)
        self.assertEqual(task.final_artifact.artifact_type, 'document_summary_report')
        self.assertIn('文档摘要报告', task.final_artifact.content.report_markdown or '')
        self.assertIsNotNone(task.result_contract)
        self.assertEqual(task.result_contract.kind, 'document_summary_report')

    def test_create_task_rejects_unsupported_task_type(self) -> None:
        with self.assertRaises(AppError) as context:
            self.service.create_task(
                TaskRequest(
                    task_type='unsupported_skill',
                    collection_name='demo',
                    doc_ids=['doc-1'],
                    instructions='总结风险点',
                )
            )

        self.assertEqual(context.exception.code, 'unsupported_task_type')

    def test_retry_task_keeps_history_and_increments_retry_count(self) -> None:
        created = self.service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块和风险点',
            )
        )

        retried = self.service.retry_task(created.task_id)

        self.assertEqual(retried.status, 'completed')
        self.assertEqual(retried.retry_count, 1)
        self.assertGreaterEqual(len(self.service.list_artifacts(created.task_id)), 4)

    def test_create_document_analysis_can_return_queued_when_dispatcher_is_async(self) -> None:
        deferred = DeferredDispatcher()
        service = TaskService(self.runtime, self.memory, self.state, deferred, self.registry)

        task = service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块和风险点',
            )
        )

        self.assertEqual(task.status, 'queued')
        self.assertIsNotNone(task.task_spec)
        self.assertIsNotNone(task.task_run)
        self.assertEqual(task.task_run.status, 'queued')
        self.assertEqual(task.task_spec.input_payload['skill_name'], 'document_analysis')
        self.assertEqual(task.task_spec.steps[0].step_id, 'load_task')
        self.assertEqual(task.task_spec.steps[-1].step_id, 'finalize')
        self.assertEqual(deferred.submitted_task_ids, [task.task_id])

    def test_create_document_analysis_rejects_missing_collection(self) -> None:
        with self.assertRaises(AppError) as context:
            self.service.create_document_analysis(
                TaskRequest(
                    collection_name='missing',
                    instructions='总结风险点',
                )
            )

        self.assertEqual(context.exception.code, 'collection_not_found')

    def test_create_document_analysis_rejects_document_outside_permission_scope(self) -> None:
        self.state.documents['doc-1']['permission'] = 'restricted'

        with self.assertRaises(AppError) as context:
            self.service.create_document_analysis(
                TaskRequest(
                    collection_name='demo',
                    doc_ids=['doc-1'],
                    instructions='总结风险点',
                    permission_scope='internal',
                )
            )

        self.assertEqual(context.exception.code, 'document_permission_denied')

    def test_create_document_analysis_filters_collection_docs_by_permission_scope(self) -> None:
        now = datetime.now(timezone.utc)
        self.state.documents['doc-2'] = {
            'doc_id': 'doc-2',
            'file_name': 'secret.md',
            'file_path': '/tmp/secret.md',
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': [],
            'checksum': 'y',
            'status': 'indexed',
            'chunk_ids': ['chunk-3'],
            'indexed_chunks': 1,
            'created_at': now,
            'updated_at': now,
            'indexed_at': now,
            'permission': 'confidential',
        }
        self.state.documents['doc-1']['permission'] = 'internal'

        task = self.service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                instructions='总结风险点',
                permission_scope='internal',
            )
        )

        self.assertEqual(task.request.doc_ids, ['doc-1'])

    def test_policy_profile_crud_hot_reloads_engine(self) -> None:
        created = self.service.create_policy_profile(
            PolicyProfileCreateRequest(
                name='org1_finance',
                version='v1',
                organization_id='org-1',
                tenant_id='tenant-1',
                allowed_roles=['analyst'],
                match_keywords=['财务'],
                blocked_tools=['review_report'],
                is_default=True,
            )
        )

        listed = self.service.list_policy_profiles(organization_id='org-1', tenant_id='tenant-1')
        self.assertEqual(listed.total, 1)
        self.assertEqual(listed.items[0].profile_id, created.profile_id)

        decision = self.service.policy_engine.check_tool(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='请做财务分析',
                organization_id='org-1',
                tenant_id='tenant-1',
                requester_role='analyst',
            ),
            'review_report',
            {},
        )
        self.assertFalse(decision.allowed)

        updated = self.service.update_policy_profile(
            created.profile_id,
            PolicyProfileUpdateRequest(blocked_tools=['finalize_report'], version='v2'),
        )
        self.assertEqual(updated.version, 'v2')

        decision_after_update = self.service.policy_engine.check_tool(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='请做财务分析',
                organization_id='org-1',
                tenant_id='tenant-1',
                requester_role='analyst',
            ),
            'finalize_report',
            {'content': {'evidence': [{'id': 'e1'}]}},
        )
        self.assertFalse(decision_after_update.allowed)

        self.service.delete_policy_profile(created.profile_id)
        self.assertEqual(self.service.list_policy_profiles().total, 0)

    def test_create_document_analysis_rejects_excessive_doc_count(self) -> None:
        now = datetime.now(timezone.utc)
        doc_ids: list[str] = []
        for index in range(65):
            doc_id = f'doc-bulk-{index}'
            doc_ids.append(doc_id)
            self.state.documents[doc_id] = {
                'doc_id': doc_id,
                'file_name': f'bulk-{index}.md',
                'file_path': f'/tmp/bulk-{index}.md',
                'file_type': 'md',
                'collection_name': 'demo',
                'tags': [],
                'checksum': f'bulk-{index}',
                'status': 'indexed',
                'chunk_ids': [f'chunk-bulk-{index}'],
                'indexed_chunks': 1,
                'created_at': now,
                'updated_at': now,
                'indexed_at': now,
                'document_title': f'批量文档 {index}',
                'document_summary': '批量导入的测试文档。',
                'document_hierarchy': f'demo / 批量文档 {index}',
            }

        with self.assertRaises(AppError) as context:
            self.service.create_document_analysis(
                TaskRequest(
                    collection_name='demo',
                    doc_ids=doc_ids,
                    instructions='总结批量文档的主要风险',
                )
            )

        self.assertEqual(context.exception.code, 'task_document_limit_exceeded')

    def test_create_document_analysis_rejects_doc_outside_collection(self) -> None:
        now = datetime.now(timezone.utc)
        self.state.documents['doc-x'] = {
            'doc_id': 'doc-x',
            'file_name': 'other.md',
            'file_path': '/tmp/other.md',
            'file_type': 'md',
            'collection_name': 'other',
            'tags': [],
            'checksum': 'y',
            'status': 'indexed',
            'chunk_ids': ['chunk-x'],
            'indexed_chunks': 1,
            'created_at': now,
            'updated_at': now,
            'indexed_at': now,
            'document_title': '其他文档',
            'document_summary': '其他集合文档',
            'document_hierarchy': 'other / 其他文档',
        }

        with self.assertRaises(AppError) as context:
            self.service.create_document_analysis(
                TaskRequest(
                    collection_name='demo',
                    doc_ids=['doc-x'],
                    instructions='总结风险点',
                )
            )

        self.assertEqual(context.exception.code, 'document_collection_mismatch')

    def test_planner_supports_review_replan(self) -> None:
        planner = TaskPlanner()
        request = TaskRequest(
            collection_name='demo',
            doc_ids=['doc-1'],
            instructions='总结核心模块和风险点',
        )

        base_plan = planner.plan(request)
        revised_plan = planner.replan_for_review(
            request,
            base_plan,
            missing_sections=['report_json'],
            unsupported_claims=['finding-1'],
        )

        self.assertEqual(revised_plan.max_steps, base_plan.max_steps)
        self.assertIn('unsupported claims 为 0', revised_plan.exit_criteria)
        self.assertIn('补齐缺失字段：report_json', revised_plan.exit_criteria)
        self.assertTrue(any(step.step_id == 's4r' for step in revised_plan.steps))
        self.assertIn('review_report', next(step for step in revised_plan.steps if step.step_id == 's4r').candidate_tools)

    def test_workflow_can_fallback_when_retrieval_and_draft_tools_fail(self) -> None:
        registry = ToolRegistry()
        for tool in [
            *build_runtime_rag_tools(),
            FailingRetrieveEvidenceTool(),
            FailingRetrieveEvidenceTool(use_graph_rag=True),
            ExtractKeyPointsTool(),
            ExtractRisksTool(),
            FailingDraftReportTool(),
            ReviewReportTool(),
            FinalizeReportTool(),
        ]:
            registry.register(tool)
        orchestrator = TaskWorkflowOrchestrator(
            TaskPlanner(),
            registry,
            self.memory,
            self.trace,
            self.settings,
            self.state,
            FakeRetrievalService(),
            FakeVectorStore(),
            None,
        )
        service = TaskService(
            AgentRuntime(orchestrator, self.memory, self.trace),
            self.memory,
            self.state,
            InlineTaskDispatcher(AgentRuntime(orchestrator, self.memory, self.trace)),
            registry,
        )

        task = service.create_document_analysis(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块、接口依赖、风险点和未决问题',
            )
        )

        self.assertEqual(task.status, 'completed')
        self.assertIsNotNone(task.final_artifact)
        self.assertIn('降级', task.final_artifact.content.report_markdown)
        self.assertTrue(
            any(
                item.kind == 'state'
                and str(item.payload.get('default_action') or '') in {'fallback', 'skip_with_gap'}
                for item in task.task_memory_entries
            )
        )

    def test_task_service_exposes_tool_schemas(self) -> None:
        schemas = self.service.list_tool_schemas()

        self.assertTrue(schemas)
        rag_load_context_schema = next(item for item in schemas if item.name == 'rag_load_document_context')
        self.assertIn('data', rag_load_context_schema.output_schema['properties'])
        self.assertNotIn('load_document_context', [item.name for item in schemas])

    def test_task_service_exposes_subagent_schemas(self) -> None:
        schemas = self.service.list_subagent_schemas()

        self.assertTrue(schemas)
        evidence_schema = next(item for item in schemas if item.name == 'evidence_agent')
        self.assertIn('rag_retrieve_evidence', evidence_schema.allowed_tools)
        self.assertTrue(any(action.action == 'collect' for action in evidence_schema.actions))
        reporting_schema = self.service.get_subagent_schema('reporting_agent')
        self.assertEqual(reporting_schema.name, 'reporting_agent')
        self.assertEqual(reporting_schema.allowed_tools, ['draft_report'])
        review_schema = self.service.get_subagent_schema('review_agent')
        self.assertEqual(review_schema.name, 'review_agent')
        contract_schema = self.service.get_subagent_schema('contract_agent')
        self.assertEqual(contract_schema.name, 'contract_agent')
        self.assertIn('read_api_contract', contract_schema.allowed_tools)

    def test_exit_criteria_gate_blocks_finalize_when_requirements_are_unreachable(self) -> None:
        orchestrator = TaskWorkflowOrchestrator(
            ImpossibleExitPlanner(),
            self.registry,
            self.memory,
            self.trace,
            self.settings,
            self.state,
            FakeRetrievalService(),
            FakeVectorStore(),
            None,
        )
        runtime = AgentRuntime(orchestrator, self.memory, self.trace)
        task = self.memory.create_task(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块和风险点',
            )
        )

        with self.assertRaises(RuntimeError) as context:
            runtime.run(task.task_id)

        latest = self.memory.get_task(task.task_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, 'failed')
        self.assertIsNone(latest.final_artifact_id)
        self.assertIn('exit criteria not satisfied', str(context.exception))

    def test_plan_guard_blocks_unknown_tools(self) -> None:
        orchestrator = TaskWorkflowOrchestrator(
            UnknownToolPlanner(),
            self.registry,
            self.memory,
            self.trace,
            self.settings,
            self.state,
            FakeRetrievalService(),
            FakeVectorStore(),
            None,
        )
        runtime = AgentRuntime(orchestrator, self.memory, self.trace)
        task = self.memory.create_task(
            TaskRequest(
                collection_name='demo',
                doc_ids=['doc-1'],
                instructions='总结核心模块和风险点',
            )
        )

        with self.assertRaises(RuntimeError) as context:
            runtime.run(task.task_id)

        self.assertIn('plan_unknown_tools', str(context.exception))


if __name__ == '__main__':
    unittest.main()

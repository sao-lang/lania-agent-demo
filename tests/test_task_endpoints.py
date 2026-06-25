import tempfile
import unittest
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models.artifact import Artifact, ReportArtifactContent
from app.models.policy import PolicyProfileItem, PolicyProfileListResponse
from app.models.task import TaskDetail, TaskListResponse, TaskMetrics, TaskRequest, TaskRunDetail, TaskRunSummary, TaskSummaryItem
from app.agents.subagents import SubAgentSchema
from app.agents.tools.base import ToolSchema
from app.core.errors import not_found_error


class FakeTaskService:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.artifact = Artifact(
            artifact_id='artifact-1',
            task_id='task-1',
            artifact_type='document_analysis_report',
            version=1,
            status='final',
            content=ReportArtifactContent(
                summary='demo summary',
                report_markdown='# 文档分析报告',
                report_json={'summary': 'demo summary'},
            ),
            created_at=now,
            updated_at=now,
        )
        self.task = TaskDetail(
            task_id='task-1',
            status='completed',
            request=TaskRequest(collection_name='demo', doc_ids=['doc-1'], instructions='总结风险点'),
            final_artifact_id='artifact-1',
            metrics=TaskMetrics(step_count=6, tool_calls=7, latency_ms=12),
            created_at=now,
            updated_at=now,
            final_artifact=self.artifact,
            artifact_ids=['artifact-1'],
        )
        self.task.ensure_runtime_contracts()
        self.task_run = TaskRunDetail(
            run_id=self.task.task_run.run_id,
            task_id=self.task.task_id,
            status='completed',
            task_type='document_analysis',
            collection_name='demo',
            instructions='总结风险点',
            created_at=now,
            completed_at=now,
            checkpoint_count=0,
            event_count=1,
            replayed_from_checkpoint_id=None,
            last_checkpoint_id=None,
            final_artifact_id='artifact-1',
            latency_ms=12,
            recoverable=False,
            request_payload=self.task.request.model_dump(mode='json'),
            task_spec=self.task.task_spec,
            task_run=self.task.task_run,
            checkpoints=[],
            run_events=[],
            context_bundles={},
            result_contract={'kind': 'document_analysis_report', 'final_artifact_id': 'artifact-1'},
        )
        self.policy = PolicyProfileItem(
            profile_id='policy-1',
            name='document_analysis_default',
            version='v1',
            is_default=True,
            created_at=now,
            updated_at=now,
        )

    def create_task(self, payload: TaskRequest) -> TaskDetail:
        task = self.task.model_copy(deep=True)
        task.request = payload
        task.ensure_runtime_contracts()
        return task

    def create_document_analysis(self, payload: TaskRequest) -> TaskDetail:
        return self.create_task(payload)

    def list_tasks(self, status=None, collection_name=None, limit=20, offset=0) -> TaskListResponse:
        return TaskListResponse(
            items=[
                TaskSummaryItem(
                    task_id='task-1',
                    task_type='document_analysis',
                    collection_name='demo',
                    status='completed',
                    final_artifact_id='artifact-1',
                    retry_count=self.task.retry_count,
                    created_at=self.task.created_at,
                    updated_at=self.task.updated_at,
                )
            ],
            total=1,
            limit=limit,
            offset=offset,
        )

    def get_task(self, task_id: str) -> TaskDetail:
        return self.task

    def list_artifacts(self, task_id: str) -> list[Artifact]:
        return [self.artifact]

    def retry_task(self, task_id: str) -> TaskDetail:
        self.task.retry_count += 1
        return self.task

    def list_task_runs(self, **kwargs) -> list[TaskRunSummary]:
        return [TaskRunSummary.model_validate(self.task_run.model_dump(mode='json'))]

    def get_task_run(self, run_id: str) -> TaskRunDetail:
        return self.task_run

    def replay_task_run(self, run_id: str, checkpoint_id: str | None = None) -> TaskRunDetail:
        return self.task_run

    def resume_task_run(self, run_id: str) -> TaskRunDetail:
        return self.task_run

    def list_tool_schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name='rag_load_document_context',
                version='v1',
                input_schema={'type': 'object'},
                output_schema={
                    'type': 'object',
                    'properties': {
                        'data': {'type': 'object'},
                        'warnings': {'type': 'array'},
                        'errors': {'type': 'array'},
                    },
                },
                trace_fields=['tool_call_id'],
            ),
        ]

    def get_tool_schema(self, tool_name: str) -> ToolSchema:
        for item in self.list_tool_schemas():
            if item.name == tool_name:
                return item
        raise not_found_error('tool', tool_name)

    def list_subagent_schemas(self) -> list[SubAgentSchema]:
        return [
            SubAgentSchema(
                name='evidence_agent',
                version='v1',
                description='collect evidence',
                allowed_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
                actions=[],
                trace_fields=['task_id', 'agent_name'],
            ),
            SubAgentSchema(
                name='reporting_agent',
                version='v1',
                description='draft report',
                allowed_tools=['draft_report'],
                actions=[],
                trace_fields=['task_id', 'agent_name'],
            ),
        ]

    def get_subagent_schema(self, agent_name: str) -> SubAgentSchema:
        return self.list_subagent_schemas()[0]

    def list_policy_profiles(self, organization_id=None, tenant_id=None, limit=20, offset=0) -> PolicyProfileListResponse:
        return PolicyProfileListResponse(total=1, limit=limit, offset=offset, items=[self.policy])

    def create_policy_profile(self, payload):
        return self.policy

    def update_policy_profile(self, profile_id, payload):
        return self.policy

    def delete_policy_profile(self, profile_id):
        return None


class FakeContainer:
    def __init__(self) -> None:
        self.task_service = FakeTaskService()


class TaskEndpointTests(unittest.TestCase):
    def _create_client(self) -> TestClient:
        settings_path = Path(tempfile.mkdtemp())
        with patch('app.core.config.get_settings') as mock_get_settings, patch(
            'app.container.build_container', return_value=FakeContainer()
        ):
            from app.core.config import Settings

            mock_get_settings.return_value = Settings(DATA_DIR=settings_path)
            sys.modules.pop('app.main', None)
            main_module = importlib.import_module('app.main')
            app = main_module.create_app()
        return TestClient(app)

    def test_create_task_endpoint_returns_task_detail(self) -> None:
        client = self._create_client()

        response = client.post(
            '/api/v1/tasks/document-analysis',
            json={
                'collection_name': 'demo',
                'doc_ids': ['doc-1'],
                'instructions': '总结风险点',
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['task_id'], 'task-1')

    def test_create_generic_task_endpoint_supports_non_document_analysis_skill(self) -> None:
        client = self._create_client()

        response = client.post(
            '/api/v1/tasks',
            json={
                'task_type': 'document_summary',
                'collection_name': 'demo',
                'doc_ids': ['doc-1'],
                'instructions': '输出摘要和风险要点',
            },
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body['request']['task_type'], 'document_summary')
        self.assertEqual(body['task_spec']['task_type'], 'document_summary')
        self.assertEqual(body['task_spec']['input_payload']['skill_name'], 'document_summary')

    def test_list_tasks_endpoint_returns_paginated_payload(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks', params={'status': 'completed', 'limit': 10, 'offset': 0})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['items'][0]['task_id'], 'task-1')

    def test_task_artifacts_endpoint_returns_versions(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/task-1/artifacts')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['artifact_id'], 'artifact-1')

    def test_retry_endpoint_delegates_to_service(self) -> None:
        client = self._create_client()

        response = client.post('/api/v1/tasks/task-1/retry')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['retry_count'], 1)

    def test_list_task_runs_endpoint_returns_run_summaries(self) -> None:
        client = self._create_client()
        task_service = client.app.state.container.task_service

        response = client.get('/api/v1/tasks/runs')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['run_id'], task_service.task.task_run.run_id)

    def test_get_task_run_endpoint_returns_run_detail(self) -> None:
        client = self._create_client()
        task_service = client.app.state.container.task_service

        response = client.get(f'/api/v1/tasks/runs/{task_service.task.task_run.run_id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task_id'], 'task-1')

    def test_list_task_tools_endpoint_returns_tool_schemas(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/tools')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        rag_tool = next(item for item in body if item['name'] == 'rag_load_document_context')
        self.assertIn('data', rag_tool['output_schema']['properties'])

    def test_get_task_tool_schema_endpoint_returns_single_schema(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/tools/rag_load_document_context')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'rag_load_document_context')

    def test_get_task_tool_schema_endpoint_returns_404_for_legacy_tool_name(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/tools/load_document_context')

        self.assertEqual(response.status_code, 404)

    def test_list_task_subagents_endpoint_returns_subagent_schemas(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/sub-agents')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['name'], 'evidence_agent')

    def test_get_task_subagent_schema_endpoint_returns_single_schema(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/sub-agents/evidence_agent')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'evidence_agent')

    def test_list_task_policies_endpoint_returns_profiles(self) -> None:
        client = self._create_client()

        response = client.get('/api/v1/tasks/document-analysis/policies')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'][0]['profile_id'], 'policy-1')

    def test_create_task_policy_endpoint_returns_profile(self) -> None:
        client = self._create_client()

        response = client.post('/api/v1/tasks/document-analysis/policies', json={'name': 'demo_policy'})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['profile_id'], 'policy-1')


if __name__ == '__main__':
    unittest.main()

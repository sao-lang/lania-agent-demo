import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel

from app.agent_platform.agents.memory import TaskMemory
from app.agent_platform.agents.tools.api_contract_tools import ListApiContractsTool, ReadApiContractTool, SearchApiContractOperationsTool
from app.agent_platform.agents.tools.artifact_capability_tools import ListArtifactsTool, ReadArtifactTool
from app.agent_platform.agents.tools.database_tools import DescribeDatabaseTableTool, ListDatabaseTablesTool, QueryDatabaseTool
from app.agent_platform.agents.tools.rag_tools import RagGroundedAnswerTool, RagGroundedQueryTool, RagLoadDocumentContextTool, RagRetrieveEvidenceTool
from app.agent_platform.agents.tools.repository_tools import ReadRepositoryFileTool, SearchRepositoryTool
from app.agent_platform.agents.tools.base import ToolContext, ToolExecutionError
from app.agent_platform.agents.tools.registry import ToolRegistry
from app.capabilities.api_contract import build_api_contract_capability
from app.capabilities.artifact import build_artifact_capability_from_provider
from app.capabilities.database import build_database_capability
from app.capabilities.knowledge import DocumentContextItem, DocumentContextResult, GroundedAnswerResult
from app.capabilities.repository import build_repository_capability
from app.agent_platform.core.config import Settings
from app.agent_platform.models.artifact import EvidenceItem, EvidencePack
from app.agent_platform.models.artifact import Artifact, ReportArtifactContent
from app.models.query import QueryResponse
from app.agent_platform.models.task import TaskRequest
from app.agent_platform.observability.trace_recorder import TraceRecorder
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class DummyInput(BaseModel):
    value: str


class DummyOutput(BaseModel):
    result: str


class DummyTool:
    name = 'dummy_tool'
    version = 'v1'
    timeout_ms = 1234
    input_model = DummyInput
    output_model = DummyOutput

    def run(self, payload: DummyInput, context: ToolContext) -> DummyOutput:
        return DummyOutput(result=payload.value.upper())


class FailingTool(DummyTool):
    name = 'failing_tool'

    def run(self, payload: DummyInput, context: ToolContext) -> DummyOutput:
        raise ConnectionError('dependency unavailable')


class FakeRagFacade:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def load_document_context(self, request):
        self.calls.append('load_document_context')
        return DocumentContextResult(
            documents=[
                DocumentContextItem(
                    doc_id='doc-1',
                    title='Demo',
                    summary='summary',
                    sections=['intro'],
                )
            ]
        )

    def retrieve_evidence(self, request, *, trace_context=None):
        self.calls.append('retrieve_evidence')
        return EvidencePack(
            task_id='',
            evidence_items=[
                EvidenceItem(
                    citation_id='c-rag',
                    source='demo.md',
                    chunk_id='chunk-rag',
                    text='rag facade evidence',
                    support_score=0.95,
                )
            ],
            coverage_score=1.0,
            missing_aspects=[],
        )

    def grounded_answer(self, request, *, trace_context=None):
        self.calls.append('grounded_answer')
        return GroundedAnswerResult(
            answer='来自 facade 的回�?,
            evidence_pack=self.retrieve_evidence(request, trace_context=trace_context),
            citations=[],
            grounded=True,
        )

    def grounded_query(self, payload, *, retrieval_query=None, trace_context=None):
        self.calls.append('grounded_query')
        return QueryResponse(
            answer=f'query::{retrieval_query or payload.question}',
            citations=[],
            retrieved_count=1,
            latency_ms=1,
            session_id=payload.session_id,
        )


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.memory = TaskMemory(self.state)
        self.registry = ToolRegistry()
        self.registry.register(DummyTool())
        self.registry.register(FailingTool())
        self.registry.register(RagLoadDocumentContextTool())
        self.registry.register(RagRetrieveEvidenceTool())
        self.registry.register(RagRetrieveEvidenceTool(use_graph_rag=True))
        self.registry.register(RagGroundedAnswerTool())
        self.registry.register(RagGroundedQueryTool())
        self.registry.register(SearchRepositoryTool())
        self.registry.register(ReadRepositoryFileTool())
        self.registry.register(ListApiContractsTool())
        self.registry.register(SearchApiContractOperationsTool())
        self.registry.register(ReadApiContractTool())
        self.registry.register(ListArtifactsTool())
        self.registry.register(ReadArtifactTool())
        self.registry.register(ListDatabaseTablesTool())
        self.registry.register(DescribeDatabaseTableTool())
        self.registry.register(QueryDatabaseTool())
        self.task = self.memory.create_task(TaskRequest(collection_name='demo', instructions='test'))
        repository_root = Path(tempfile.mkdtemp())
        (repository_root / 'demo.txt').write_text('alpha\nbeta capability\n', encoding='utf-8')
        (repository_root / 'openapi.json').write_text(
            '{"openapi":"3.0.0","info":{"title":"Demo API","version":"v1"},"paths":{"/health":{"get":{"operationId":"getHealth","summary":"health endpoint","tags":["system"]}}}}',
            encoding='utf-8',
        )
        persistence = SQLiteStateStore(self.settings)
        persistence.upsert_task({'task_id': self.task.task_id, 'status': 'pending', 'instructions': 'test'})
        artifact = Artifact(
            artifact_id='artifact-1',
            task_id=self.task.task_id,
            artifact_type='document_analysis_report',
            version=1,
            status='final',
            content=ReportArtifactContent(summary='artifact summary', confidence=0.9),
            created_at=self.task.created_at,
            updated_at=self.task.updated_at,
        )
        persistence.upsert_artifact(artifact.model_dump(mode='python'))
        self.fake_rag = FakeRagFacade()
        self.context = ToolContext(
            state=self.state,
            retrieval=None,
            trace=self.trace,
            task_memory=self.memory,
            settings=self.settings,
            deps={
                'knowledge': None,
                'rag': self.fake_rag,
                'repository': build_repository_capability(repository_root),
                'api_contract': build_api_contract_capability(repository_root),
                'artifact': build_artifact_capability_from_provider(
                    settings=self.settings,
                    state=self.state,
                    persistence=persistence,
                ),
                'database': build_database_capability(self.settings),
            },
            task_id=self.task.task_id,
            step_name='unit_test',
        )

    def test_describe_returns_tool_schema(self) -> None:
        schema = self.registry.describe('dummy_tool')

        self.assertEqual(schema.name, 'dummy_tool')
        self.assertEqual(schema.version, 'v1')
        self.assertEqual(schema.timeout_ms, 1234)
        self.assertIn('properties', schema.input_schema)
        self.assertTrue(schema.error_codes)
        self.assertIn('data', schema.output_schema['properties'])
        self.assertIn('warnings', schema.output_schema['properties'])
        self.assertIn('errors', schema.output_schema['properties'])

    def test_run_wraps_runtime_error_and_records_structured_tool_call(self) -> None:
        with self.assertRaises(ToolExecutionError) as context:
            self.registry.run('failing_tool', {'value': 'x'}, self.context)

        self.assertEqual(context.exception.error_type, 'dependency_error')
        self.assertEqual(context.exception.default_action, 'fallback')
        latest = self.memory.get_task(self.task.task_id)
        assert latest is not None
        self.assertTrue(latest.tool_call_history)
        record = latest.tool_call_history[-1]
        self.assertEqual(record.tool_name, 'failing_tool')
        self.assertEqual(record.error_type, 'dependency_error')
        self.assertEqual(record.default_action, 'fallback')
        self.assertTrue(record.tool_call_id.startswith('tool-'))
        self.assertIn('errors', record.output_summary)

    def test_rag_tools_run_via_rag_facade(self) -> None:
        context_result = self.registry.run(
            'rag_load_document_context',
            {'collection_name': 'demo', 'doc_ids': ['doc-1']},
            self.context,
        )
        evidence_result = self.registry.run(
            'rag_retrieve_evidence',
            {'query': 'risk', 'collection_name': 'demo'},
            self.context,
        )
        answer_result = self.registry.run(
            'rag_grounded_answer',
            {'question': '能力是否启用', 'collection_name': 'demo'},
            self.context,
        )
        query_result = self.registry.run(
            'rag_grounded_query',
            {'question': '能力是否启用', 'collection_name': 'demo', 'retrieval_query': 'rewritten::能力是否启用'},
            self.context,
        )

        self.assertEqual(context_result.documents[0].doc_id, 'doc-1')
        self.assertEqual(evidence_result.evidence_items[0].chunk_id, 'chunk-rag')
        self.assertEqual(answer_result.answer, '来自 facade 的回�?)
        self.assertEqual(query_result.answer, 'query::rewritten::能力是否启用')
        self.assertEqual(
            self.fake_rag.calls,
            ['load_document_context', 'retrieve_evidence', 'grounded_answer', 'retrieve_evidence', 'grounded_query'],
        )

    def test_repository_tools_run_via_repository_capability(self) -> None:
        search_result = self.registry.run(
            'search_repository',
            {'query': 'capability', 'path_prefix': '.', 'max_results': 5},
            self.context,
        )
        read_result = self.registry.run(
            'read_repository_file',
            {'path': 'demo.txt', 'start_line': 1, 'max_lines': 5},
            self.context,
        )

        self.assertEqual(search_result.matches[0].path, 'demo.txt')
        self.assertIn('beta capability', read_result.content)

    def test_api_contract_tools_run_via_api_contract_capability(self) -> None:
        list_result = self.registry.run(
            'list_api_contracts',
            {'path_prefix': '.', 'max_entries': 10},
            self.context,
        )
        search_result = self.registry.run(
            'search_api_contract_operations',
            {'query': 'health endpoint', 'path_prefix': '.', 'max_results': 5},
            self.context,
        )
        read_result = self.registry.run(
            'read_api_contract',
            {'path': 'openapi.json', 'method': 'get', 'endpoint_path': '/health'},
            self.context,
        )

        self.assertEqual(list_result.contracts[0].path, 'openapi.json')
        self.assertEqual(search_result.matches[0].operation_id, 'getHealth')
        self.assertIsNotNone(read_result.selected_operation)
        self.assertEqual(read_result.selected_operation.path, '/health')

    def test_database_tools_run_via_database_capability(self) -> None:
        list_result = self.registry.run(
            'list_database_tables',
            {'include_system_tables': False, 'max_entries': 20},
            self.context,
        )
        describe_result = self.registry.run(
            'describe_database_table',
            {'table_name': 'tasks'},
            self.context,
        )
        query_result = self.registry.run(
            'query_database',
            {'sql': 'SELECT task_id, payload FROM tasks', 'max_rows': 10},
            self.context,
        )

        self.assertIn('tasks', [item.name for item in list_result.tables])
        self.assertIn('task_id', [item.name for item in describe_result.columns])
        self.assertEqual(query_result.rows[0]['task_id'], self.task.task_id)

    def test_artifact_tools_run_via_artifact_capability(self) -> None:
        list_result = self.registry.run(
            'list_artifacts',
            {'task_id': self.task.task_id, 'limit': 10, 'offset': 0},
            self.context,
        )
        read_result = self.registry.run(
            'read_artifact',
            {'artifact_id': 'artifact-1'},
            self.context,
        )

        self.assertEqual(list_result.total, 1)
        self.assertEqual(list_result.items[0].artifact_id, 'artifact-1')
        self.assertEqual(read_result.content.summary, 'artifact summary')


if __name__ == '__main__':
    unittest.main()

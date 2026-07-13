import tempfile
import unittest
from pathlib import Path

import httpx

from app.agent_platform.agents.memory import TaskMemory
from app.agent_platform.agents.subagents import (
    ContractAgent,
    ContractDiscoverInput,
    EvidenceAgent,
    EvidenceCollectionInput,
    SubAgentHandoff,
    SubAgentRegistry,
    SubAgentRuntime,
)
from app.capabilities.api_contract import LocalApiContractCapability, build_api_contract_capability_from_provider
from app.capabilities.artifact import LocalArtifactCapability, build_artifact_capability_from_provider
from app.capabilities.database import LocalSQLiteDatabaseCapability, build_database_capability_from_provider
from app.capabilities.knowledge import DefaultKnowledgeCapability, build_knowledge_capability
from app.capabilities.knowledge.base import DocumentContextRequest, GroundedAnswerRequest, KnowledgeSearchRequest
from app.capabilities.knowledge.remote import RemoteKnowledgeCapability, RemoteKnowledgeProviderError
from app.capabilities.knowledge.contracts import GroundedAnswerStrategy
from app.agent_platform.core.config import Settings
from app.agent_platform.harness.sandbox import ToolSandbox, build_default_sandbox_worker_registry
from app.agent_platform.harness.model_router import ModelRouter
from app.agent_platform.models.artifact import EvidenceItem, EvidencePack, ReportArtifactContent
from app.agent_platform.agents.tools.base import ToolExecutionError
from app.agent_platform.models.task import RunBudget, TaskRequest
from app.agent_platform.observability.trace_recorder import TraceRecorder
from app.services.state import InMemoryState
from app.workflows.tasks.skill import build_default_task_skill_registry


class _FakeRetrievalService:
    def retrieve(self, collection_name, question, top_k, **kwargs):
        return []


class P2RuntimeExtensionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))

    def test_model_router_selects_profile_by_budget_and_purpose(self) -> None:
        router = ModelRouter()

        economy = router.route(
            purpose='task_analysis',
            llm_available=True,
            feature_enabled=True,
            run_budget=RunBudget(max_steps=4, max_step_turns=2, max_tool_calls=8, top_k=4),
            step_name='analyze',
            evidence_count=2,
        )
        quality = router.route(
            purpose='task_review',
            llm_available=True,
            feature_enabled=True,
            run_budget=RunBudget(max_steps=8, max_step_turns=2, max_tool_calls=16, top_k=6),
            step_name='review_artifact',
            evidence_count=6,
        )

        self.assertEqual(economy.mode, 'llm')
        self.assertEqual(economy.profile, 'economy')
        self.assertGreater(economy.estimated_cost_units, 0.0)
        self.assertEqual(quality.profile, 'quality')
        self.assertGreater(quality.estimated_cost_units, economy.estimated_cost_units)

    def test_build_knowledge_capability_uses_provider_registry(self) -> None:
        router = ModelRouter()
        capability = build_knowledge_capability(
            settings=self.settings,
            state=InMemoryState(),
            retrieval=_FakeRetrievalService(),
            vector_store=None,
            llm=None,
            model_router=router,
        )

        self.assertIsInstance(capability, DefaultKnowledgeCapability)
        self.assertIs(capability.model_router, router)

    def test_build_knowledge_capability_supports_remote_http_provider(self) -> None:
        capability = build_knowledge_capability(
            settings=Settings(
                DATA_DIR=Path(tempfile.mkdtemp()),
                KNOWLEDGE_CAPABILITY_PROVIDER='remote_http',
                KNOWLEDGE_CAPABILITY_BASE_URL='http://knowledge.test',
            ),
            state=InMemoryState(),
            retrieval=_FakeRetrievalService(),
            vector_store=None,
            llm=None,
        )

        self.assertIsInstance(capability, RemoteKnowledgeCapability)
        self.assertEqual(capability.base_url, 'http://knowledge.test')

    def test_build_knowledge_capability_reuses_injected_local_fallback(self) -> None:
        local_capability = DefaultKnowledgeCapability(
            InMemoryState(),
            _FakeRetrievalService(),
            vector_store=None,
            llm=None,
        )
        capability = build_knowledge_capability(
            settings=Settings(
                DATA_DIR=Path(tempfile.mkdtemp()),
                KNOWLEDGE_CAPABILITY_PROVIDER='remote_http',
                KNOWLEDGE_CAPABILITY_BASE_URL='http://knowledge.test',
                KNOWLEDGE_CAPABILITY_ALLOW_LOCAL_FALLBACK=True,
            ),
            state=InMemoryState(),
            retrieval=_FakeRetrievalService(),
            vector_store=None,
            llm=None,
            local_fallback_capability=local_capability,
        )

        self.assertIsInstance(capability, RemoteKnowledgeCapability)
        self.assertIs(capability.fallback_capability, local_capability)

    def test_build_api_contract_capability_uses_provider_registry(self) -> None:
        capability = build_api_contract_capability_from_provider(settings=self.settings)

        self.assertIsInstance(capability, LocalApiContractCapability)
        self.assertEqual(capability.root_path, Path.cwd().resolve())

    def test_build_artifact_capability_uses_provider_registry(self) -> None:
        state = InMemoryState()
        capability = build_artifact_capability_from_provider(
            settings=self.settings,
            state=state,
            persistence=None,
        )

        self.assertIsInstance(capability, LocalArtifactCapability)

    def test_build_database_capability_uses_provider_registry(self) -> None:
        capability = build_database_capability_from_provider(settings=self.settings)

        self.assertIsInstance(capability, LocalSQLiteDatabaseCapability)
        self.assertEqual(capability.db_path, self.settings.sqlite_db_path.resolve())

    def test_default_task_skill_registry_registers_builtin_skill(self) -> None:
        registry = build_default_task_skill_registry()

        self.assertTrue(registry.has('document_analysis'))
        self.assertTrue(registry.has('document_summary'))
        self.assertEqual([skill.skill_name for skill in registry.list()], ['document_analysis', 'document_summary'])

    def test_subagent_runtime_persists_handoff_audit_fields(self) -> None:
        state = InMemoryState()
        memory = TaskMemory(state)
        task = memory.create_task(TaskRequest(collection_name='demo', doc_ids=['doc-1'], instructions='总结风险'))
        trace = TraceRecorder()
        registry = SubAgentRegistry()
        registry.register(EvidenceAgent(memory, trace))
        runtime = SubAgentRuntime(registry, trace)

        result = runtime.execute(
            'evidence_agent',
            'collect',
            EvidenceCollectionInput(
                task_id=task.task_id,
                query='总结风险',
                collection_name='demo',
                doc_ids=['doc-1'],
                top_k=3,
                focus_aspects=['risk'],
            ),
            handoff=SubAgentHandoff(
                source_step_id='retrieve_evidence',
                context_keys=['task.request.instructions', 'focus_aspects'],
                step_limit=1,
                budget_limit=2,
                sandbox_profile='restricted',
            ),
            runner=lambda tool_name, payload: EvidencePack(
                task_id=task.task_id,
                evidence_items=[
                    EvidenceItem(
                        citation_id='c1',
                        source='design.md',
                        chunk_id='chunk-1',
                        text='系统存在异常处理缺失风险�?,
                        support_score=0.9,
                    )
                ],
                coverage_score=1.0,
                missing_aspects=[],
            ),
            merge_packs=lambda left, right: left,
        )

        stored = memory.get_task(task.task_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(result.decision, 'continue')
        self.assertEqual(len(stored.sub_agent_runs), 1)
        run = stored.sub_agent_runs[0]
        self.assertEqual(run.source_step_id, 'retrieve_evidence')
        self.assertEqual(run.context_keys, ['task.request.instructions', 'focus_aspects'])
        self.assertEqual(run.step_limit, 1)
        self.assertEqual(run.budget_limit, 2)
        self.assertEqual(run.sandbox_profile, 'restricted')
        self.assertTrue(run.handoff_id)

    def test_contract_subagent_can_discover_contracts_without_runtime_changes(self) -> None:
        state = InMemoryState()
        memory = TaskMemory(state)
        task = memory.create_task(TaskRequest(collection_name='demo', doc_ids=['doc-1'], instructions='读取 API 契约'))
        trace = TraceRecorder()
        registry = SubAgentRegistry()
        registry.register(ContractAgent(memory, trace))
        runtime = SubAgentRuntime(registry, trace)

        result = runtime.execute(
            'contract_agent',
            'discover',
            ContractDiscoverInput(task_id=task.task_id, query='health', path_prefix='.', max_results=5),
            runner=lambda tool_name, payload: type(
                'SearchResult',
                (),
                {
                    'matches': [
                        {
                            'contract_path': 'openapi.yaml',
                            'method': 'get',
                            'path': '/health',
                            'operation_id': 'getHealth',
                            'summary': 'health endpoint',
                            'tags': ['system'],
                        }
                    ]
                },
            )(),
        )

        stored = memory.get_task(task.task_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(result.decision, 'inspect_contract')
        self.assertEqual(result.operation_matches[0].operation_id, 'getHealth')
        self.assertEqual(stored.sub_agent_runs[-1].agent_name, 'contract_agent')

    def test_default_sandbox_worker_registry_supports_multiple_tools(self) -> None:
        registry = build_default_sandbox_worker_registry()

        self.assertTrue(registry.has('draft_report'))
        self.assertTrue(registry.has('review_report'))
        self.assertTrue(registry.has('finalize_report'))
        self.assertEqual([tool.tool_name for tool in registry.list()], ['draft_report', 'finalize_report', 'review_report'])

    def test_tool_sandbox_local_isolated_uses_injected_registry(self) -> None:
        registry = build_default_sandbox_worker_registry()
        registry._tools.pop('finalize_report')
        sandbox = ToolSandbox(worker_registry=registry)

        with self.assertRaises(ToolExecutionError) as context:
            sandbox.execute_local_isolated(
                tool_name='finalize_report',
                payload={
                    'content': ReportArtifactContent(summary='done'),
                    'review': None,
                    'output_format': 'markdown+json',
                },
                timeout_ms=1000,
                output_model=ReportArtifactContent,
            )

        self.assertTrue(context.exception.code.endswith('sandbox_not_supported'))

    def test_remote_knowledge_capability_calls_http_service(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/api/v1/knowledge/document-context':
                return httpx.Response(200, json={'documents': [{'doc_id': 'doc-1', 'title': 'Doc', 'summary': 'S', 'sections': [], 'metadata': {}}]})
            if request.url.path == '/api/v1/knowledge/search':
                return httpx.Response(
                    200,
                    json={
                        'task_id': 'task-1',
                        'evidence_items': [
                            {
                                'citation_id': 'c1',
                                'source': 'demo.md',
                                'chunk_id': 'chunk-1',
                                'text': 'risk evidence',
                                'support_score': 0.8,
                                'page': None,
                                'tags': ['risk'],
                            }
                        ],
                        'coverage_score': 1.0,
                        'missing_aspects': [],
                    },
                )
            if request.url.path == '/api/v1/knowledge/grounded-answer':
                return httpx.Response(
                    200,
                    json={
                        'answer': 'grounded',
                        'evidence_pack': {
                            'task_id': 'task-1',
                            'evidence_items': [],
                            'coverage_score': 1.0,
                            'missing_aspects': [],
                        },
                        'citations': [],
                        'grounded': True,
                        'quality_report': {'enabled': False},
                    },
                )
            return httpx.Response(404)

        client = httpx.Client(base_url='http://knowledge.test', transport=httpx.MockTransport(handler))
        capability = RemoteKnowledgeCapability(base_url='http://knowledge.test', client=client)

        context = capability.load_document_context(DocumentContextRequest(collection_name='demo', doc_ids=['doc-1']))
        evidence = capability.retrieve_evidence(KnowledgeSearchRequest(query='risk', collection_name='demo'))
        answer = capability.grounded_answer(
            GroundedAnswerRequest(
                question='risk?',
                collection_name='demo',
                strategy=GroundedAnswerStrategy(),
            )
        )

        self.assertEqual(context.documents[0].doc_id, 'doc-1')
        self.assertEqual(evidence.evidence_items[0].citation_id, 'c1')
        self.assertEqual(answer.answer, 'grounded')

    def test_remote_knowledge_capability_falls_back_on_upstream_error(self) -> None:
        class _FallbackKnowledge:
            def load_document_context(self, request):
                raise AssertionError('unexpected')

            def retrieve_evidence(self, request, *, trace_context=None):
                return EvidencePack(task_id='task-fallback', evidence_items=[], coverage_score=0.8, missing_aspects=['fallback'])

            def grounded_answer(self, request, *, trace_context=None):
                raise AssertionError('unexpected')

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={'error': 'unavailable'})

        trace: list[dict] = []
        client = httpx.Client(base_url='http://knowledge.test', transport=httpx.MockTransport(handler))
        capability = RemoteKnowledgeCapability(
            base_url='http://knowledge.test',
            client=client,
            fallback_capability=_FallbackKnowledge(),
            allow_local_fallback=True,
        )

        result = capability.retrieve_evidence(
            KnowledgeSearchRequest(query='risk', collection_name='demo'),
            trace_context={'trace': trace},
        )

        self.assertEqual(result.task_id, 'task-fallback')
        self.assertTrue(any(item.get('event') == 'knowledge_remote_fallback_applied' for item in trace))

    def test_remote_knowledge_capability_surfaces_auth_failure_without_fallback(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={'error': 'unauthorized'})

        client = httpx.Client(base_url='http://knowledge.test', transport=httpx.MockTransport(handler))
        capability = RemoteKnowledgeCapability(
            base_url='http://knowledge.test',
            client=client,
            allow_local_fallback=False,
        )

        with self.assertRaises(RemoteKnowledgeProviderError) as context:
            capability.retrieve_evidence(KnowledgeSearchRequest(query='risk', collection_name='demo'))

        self.assertEqual(context.exception.code, 'knowledge_remote_auth_failed')

    def test_remote_sandbox_executor_falls_back_to_local_worker(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={'error': 'unavailable'})

        settings = Settings(
            DATA_DIR=Path(tempfile.mkdtemp()),
            SANDBOX_EXECUTOR_PROVIDER='remote_http',
            SANDBOX_EXECUTOR_BASE_URL='http://sandbox.test',
            SANDBOX_EXECUTOR_ALLOW_LOCAL_FALLBACK=True,
        )
        sandbox = ToolSandbox(
            settings,
            client=httpx.Client(base_url='http://sandbox.test', transport=httpx.MockTransport(handler)),
        )

        result = sandbox.execute_isolated(
            tool_name='finalize_report',
            payload={
                'content': {
                    'summary': 'draft',
                    'key_findings': [],
                    'risks': [],
                    'evidence': [],
                    'open_questions': [],
                    'confidence': 0.9,
                },
                'output_format': 'markdown+json',
            },
            timeout_ms=3000,
            output_model=ReportArtifactContent,
        )

        self.assertEqual(result.summary, 'draft')


if __name__ == '__main__':
    unittest.main()

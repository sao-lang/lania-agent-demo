import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.capabilities.knowledge import GroundedAnswerResult
from app.core.config import Settings
from app.models.artifact import EvidenceItem, EvidencePack
from app.models.query import ChatRequest, QueryRequest, QueryResponse
from app.rag.observability import TraceRecorder
from app.workflows.query_task_adapter import build_query_task_spec
from app.workflows.query_orchestrator import QueryWorkflowOrchestrator


class FakeQueryEngine:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def query(self, payload: QueryRequest) -> QueryResponse:
        self.calls.append(f'query:{payload.question}')
        return QueryResponse(
            answer=f'query::{payload.question}',
            citations=[],
            retrieved_count=0,
            latency_ms=1,
            session_id=payload.session_id,
        )

    def chat(self, payload: ChatRequest) -> QueryResponse:
        self.calls.append(f'chat:{payload.question}')
        return QueryResponse(
            answer=f'chat::{payload.question}',
            citations=[],
            retrieved_count=0,
            latency_ms=1,
            session_id=payload.session_id,
        )

    def stream_query(self, payload: QueryRequest):
        self.calls.append(f'stream_query:{payload.question}')
        yield {'event': 'start', 'data': {'mode': 'query'}}
        yield {'event': 'done', 'data': {'response': self.query(payload).model_dump()}}

    def stream_chat(self, payload: ChatRequest):
        self.calls.append(f'stream_chat:{payload.question}')
        yield {'event': 'start', 'data': {'mode': 'chat_stream'}}
        yield {'event': 'done', 'data': {'response': self.chat(payload).model_dump()}}

    def get_session(self, session_id: str):
        self.calls.append(f'get_session:{session_id}')
        return None

    def list_sessions(self):
        self.calls.append('list_sessions')
        return []

    def summarize_session(self, session_id: str):
        self.calls.append(f'summarize_session:{session_id}')
        return None


class CapabilityReadyEngine(FakeQueryEngine):
    def __init__(self) -> None:
        super().__init__()
        self.cache_store_calls: list[str] = []

    def _check_guardrails(self, question: str, payload: QueryRequest, trace_context: str):
        return {
            'blocked': False,
            'sanitized_question': question,
            'prompt_guardrails_enabled': False,
        }

    def _prepare_retrieval_question(self, question: str, use_query_rewrite: bool, trace_context: str) -> str:
        return f'rewritten::{question}' if use_query_rewrite else question

    def _question_for_storage(self, question: str, guardrail_state):
        return question

    def _lookup_semantic_cache(self, payload: QueryRequest, question: str, cache_mode: str):
        return None, {'hit': False}

    def _sanitize_citations(self, citations, payload: QueryRequest, trace_context: str):
        return citations, {'applied': False}

    def _sanitize_text(self, text: str, payload: QueryRequest, target: str, trace_context: str):
        return text, {'applied': False}

    def _store_semantic_cache(self, payload: QueryRequest, question: str, cache_mode: str, response, answer_mode: str, metadata):
        self.cache_store_calls.append(answer_mode)

    def _public_guardrail_state(self, guardrail_state, citation_redaction=None, answer_redaction=None):
        return {'blocked': False}


class FakeKnowledgeCapability:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def grounded_answer(self, request, *, trace_context=None):
        self.calls.append(request.retrieval_query or request.question)
        return GroundedAnswerResult(
            answer='依据证据的回答',
            evidence_pack=EvidencePack(
                task_id='',
                evidence_items=[
                    EvidenceItem(
                        citation_id='c1',
                        source='demo.md',
                        chunk_id='chunk-1',
                        text='文档说明该接口用于压缩历史消息。',
                        support_score=0.91,
                    )
                ],
                coverage_score=1.0,
                missing_aspects=[],
            ),
            citations=[],
            grounded=True,
        )


class FailingStepEngine(FakeQueryEngine):
    def __init__(self) -> None:
        super().__init__()
        self.state = object()
        self.llm = object()
        self.retrieval_service = type('RetrievalService', (), {'vector_store': object()})()

    def _check_guardrails(self, question: str, payload: QueryRequest, trace_context: str):
        raise RuntimeError('guardrail node boom')


class QueryOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace = TraceRecorder()
        self.engine = FakeQueryEngine()
        self.data_dir = Path(tempfile.mkdtemp())

    def test_settings_expose_langgraph_defaults(self) -> None:
        settings = Settings(DATA_DIR=self.data_dir)

        self.assertEqual(settings.query_orchestrator, 'langgraph')
        self.assertFalse(settings.enable_self_rag_retry)
        self.assertEqual(settings.self_rag_max_retry_count, 1)
        self.assertEqual(settings.self_rag_min_grounding_confidence, 0.65)
        self.assertFalse(settings.enable_query_run_auto_recovery)
        self.assertEqual(settings.query_run_auto_recovery_limit, 20)

    def test_default_mode_runs_query_via_workflow(self) -> None:
        orchestrator = QueryWorkflowOrchestrator(Settings(DATA_DIR=self.data_dir), self.engine, self.trace)
        expected = QueryResponse(
            answer='graph::hello',
            citations=[],
            retrieved_count=0,
            latency_ms=1,
            session_id=None,
        )

        with patch.object(orchestrator, '_invoke_workflow', return_value={'result': expected}):
            response = orchestrator.query(QueryRequest(question='hello', collection_name='demo'))

        self.assertEqual(response.answer, 'graph::hello')
        self.assertEqual(self.engine.calls, [])

    def test_query_uses_workflow_even_when_knowledge_capability_is_available(self) -> None:
        engine = CapabilityReadyEngine()
        capability = FakeKnowledgeCapability()
        orchestrator = QueryWorkflowOrchestrator(
            Settings(DATA_DIR=self.data_dir),
            engine,
            self.trace,
            knowledge_capability=capability,
        )
        expected = QueryResponse(
            answer='graph::hello',
            citations=[],
            retrieved_count=0,
            latency_ms=1,
            session_id=None,
        )

        with patch.object(orchestrator, '_invoke_workflow', return_value={'result': expected}):
            response = orchestrator.query(
                QueryRequest(
                    question='hello',
                    collection_name='demo',
                    use_query_rewrite=True,
                )
            )

        self.assertEqual(response.answer, 'graph::hello')
        self.assertEqual(capability.calls, [])
        self.assertEqual(engine.calls, [])

    def test_stream_query_returns_error_event_when_workflow_fails(self) -> None:
        orchestrator = QueryWorkflowOrchestrator(
            Settings(DATA_DIR=self.data_dir, QUERY_ORCHESTRATOR='langgraph'),
            self.engine,
            self.trace,
        )
        orchestrator._invoke_workflow = lambda payload, mode: (_ for _ in ()).throw(RuntimeError('workflow boom'))  # type: ignore[method-assign]

        events = list(orchestrator.stream_query(QueryRequest(question='hello', collection_name='demo')))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event'], 'error')
        self.assertEqual(events[0]['data']['code'], 'workflow_failed')
        self.assertIn('workflow boom', events[0]['data']['message'])

    def test_stream_query_emits_step_failed_before_error_when_node_crashes(self) -> None:
        engine = FailingStepEngine()
        orchestrator = QueryWorkflowOrchestrator(
            Settings(DATA_DIR=self.data_dir, QUERY_ORCHESTRATOR='langgraph'),
            engine,
            self.trace,
        )

        events = list(orchestrator.stream_query(QueryRequest(question='hello', collection_name='demo')))

        self.assertEqual(events[0]['event'], 'step_failed')
        self.assertEqual(events[1]['event'], 'error')
        self.assertTrue(any(event.name == 'workflow_step_failed' for event in self.trace.events))

    def test_langgraph_mode_runs_workflow_and_records_trace(self) -> None:
        self.engine.settings = Settings(DATA_DIR=self.data_dir, QUERY_ORCHESTRATOR='langgraph')  # type: ignore[attr-defined]
        self.engine.state = object()  # type: ignore[attr-defined]
        self.engine.llm = object()  # type: ignore[attr-defined]
        self.engine.retrieval_service = type('RetrievalService', (), {'vector_store': object()})()  # type: ignore[attr-defined]
        orchestrator = QueryWorkflowOrchestrator(
            Settings(DATA_DIR=self.data_dir, QUERY_ORCHESTRATOR='langgraph'),
            self.engine,
            self.trace,
        )
        expected = QueryResponse(
            answer='chat::hi',
            citations=[],
            retrieved_count=0,
            latency_ms=1,
            session_id='s-1',
        )
        test_case = self

        class FakeCompiledGraph:
            def invoke(self, state):
                test_case.assertEqual(state['graph_entry_route'], 'check_guardrails')
                test_case.assertEqual(state['task_spec'].task_type, 'session_chat')
                test_case.assertEqual(state['task_spec'].steps[0].step_id, 'check_guardrails')
                test_case.assertEqual(state['task_spec'].steps[1].step_id, 'load_session_context')
                test_case.assertEqual(state['metadata']['task_steps'][0], 'check_guardrails')
                test_case.trace.record(
                    'workflow_node_completed',
                    {
                        'workflow': 'langgraph',
                        'node': 'finalize',
                        'mode': state['mode'],
                        'collection_name': state['request'].collection_name,
                    },
                )
                return {
                    **state,
                    'result': expected,
                    'events': [],
                }

        with patch.object(QueryWorkflowOrchestrator, '_get_query_app', return_value=FakeCompiledGraph()) as app_mock:
            response = orchestrator.chat(
                ChatRequest(
                    question='hi',
                    collection_name='demo',
                    session_id='s-1',
                )
            )

        self.assertEqual(app_mock.call_count, 1)

        self.assertEqual(response.answer, 'chat::hi')
        event_names = [event.name for event in self.trace.events]
        self.assertIn('workflow_started', event_names)
        self.assertIn('workflow_node_completed', event_names)
        self.assertIn('workflow_completed', event_names)
        workflow_started = next(event for event in self.trace.events if event.name == 'workflow_started')
        self.assertEqual(workflow_started.payload['task_type'], 'session_chat')
        self.assertEqual(workflow_started.payload['task_steps'][0], 'check_guardrails')

    def test_query_request_can_be_projected_to_task_spec(self) -> None:
        task_spec = build_query_task_spec(
            QueryRequest(
                question='hello',
                collection_name='demo',
                use_graph_rag=True,
            ),
            mode='query',
        )

        self.assertEqual(task_spec.task_type, 'grounded_query')
        self.assertEqual(task_spec.run_budget.top_k, 5)
        self.assertEqual(
            [step.step_id for step in task_spec.steps],
            [
                'check_guardrails',
                'rewrite_query',
                'expand_queries',
                'lookup_cache',
                'retrieve_evidence',
                'compress_context',
                'grounded_answer',
                'self_reflect',
            ],
        )
        self.assertTrue(task_spec.input_payload['retrieval_options']['use_graph_rag'])

    def test_chat_request_can_be_projected_to_task_spec(self) -> None:
        task_spec = build_query_task_spec(
            ChatRequest(
                question='hi',
                collection_name='demo',
                session_id='s-1',
            ),
            mode='chat_stream',
        )

        self.assertEqual(task_spec.task_type, 'session_chat')
        self.assertEqual(task_spec.steps[0].step_id, 'check_guardrails')
        self.assertEqual(task_spec.steps[1].step_id, 'load_session_context')
        self.assertEqual(task_spec.steps[-1].step_id, 'persist_session')
        self.assertIn('rewrite_query', [step.step_id for step in task_spec.steps])
        self.assertEqual(task_spec.input_payload['session_id'], 's-1')


if __name__ == '__main__':
    unittest.main()

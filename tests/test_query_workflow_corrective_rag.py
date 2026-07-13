import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_platform.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.agent_platform.observability.trace_recorder import TraceRecorder
from app.rag_system.query.engine import RagQueryEngine
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.workflows.query_nodes import QueryWorkflowNodes
from app.workflows.query_orchestrator import QueryWorkflowOrchestrator
from app.workflows.query_runtime import ensure_query_workflow_runtime


class FakeCorrectiveRetrievalService:
    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()
        self.calls: list[str] = []

    def rewrite_query(self, question: str) -> str:
        return question

    def retrieve(
        self,
        collection_name: str,
        question: str,
        top_k: int,
        filters=None,
        use_hybrid_retrieval: bool = False,
        use_rerank: bool = True,
        use_long_context_reorder: bool = False,
    ) -> list[CitationItem]:
        self.calls.append(question)
        return [
            CitationItem(
                chunk_id='c1',
                source='demo.md',
                text='session summary 接口用于压缩历史消息，并生成会话摘要�?,
                score=0.93,
            )
        ]


class FakeCorrectiveLLM:
    def __init__(self) -> None:
        self.answer_call_count = 0

    def complete(self, prompt: str) -> str:
        if '你是 RAG 结果校验�? in prompt:
            if '自动同步外部 CRM 数据' in prompt:
                return json.dumps(
                    {
                        'supported': False,
                        'confidence': 0.08,
                        'risk': 'high',
                        'reason': 'contains_unsupported_claim',
                        'rewrite_needed': True,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    'supported': True,
                    'confidence': 0.92,
                    'risk': 'low',
                    'reason': 'grounded_by_context',
                    'rewrite_needed': False,
                },
                ensure_ascii=False,
            )
        if '你是一个严格保守的 RAG 助手' in prompt:
            return 'session summary 接口用于压缩历史消息，并生成会话摘要�?
        self.answer_call_count += 1
        if self.answer_call_count == 1:
            return 'session summary 接口还会自动同步外部 CRM 数据�?
        return 'session summary 接口用于压缩历史消息，并生成会话摘要�?


class QueryWorkflowCorrectiveRagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()), QUERY_ORCHESTRATOR='langgraph')
        self.state = InMemoryState()
        self.persistence = SQLiteStateStore(self.settings)
        self.trace = TraceRecorder()
        self.retrieval = FakeCorrectiveRetrievalService()
        self.fake_llm = FakeCorrectiveLLM()

    def _build_orchestrator(self) -> QueryWorkflowOrchestrator:
        with patch('app.rag.query_engine.build_llm', return_value=self.fake_llm):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)
        return QueryWorkflowOrchestrator(self.settings, engine, self.trace, self.state, self.persistence)

    def test_query_runs_on_langgraph_corrective_path(self) -> None:
        orchestrator = self._build_orchestrator()

        response = orchestrator.query(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            )
        )

        self.assertEqual(response.answer, 'session summary 接口用于压缩历史消息，并生成会话摘要�?)
        workflow_events = [event for event in self.trace.events if event.name == 'workflow_node_completed']
        self.assertTrue(any(event.payload.get('node') == 'self_reflect' for event in workflow_events))
        self.assertFalse(any(event.payload.get('node') == 'load_request' for event in workflow_events))
        self.assertTrue(any(event.name == 'self_rag_decision' for event in self.trace.events))

    def test_query_workflow_exposes_structured_task_run_and_reflection_decision(self) -> None:
        orchestrator = self._build_orchestrator()

        state = orchestrator._invoke_workflow(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            ),
            mode='query',
        )

        self.assertEqual(state['task_run'].status, 'completed')
        self.assertIn('self_reflect', state['task_run'].step_runtimes)
        self.assertEqual(state['task_run'].step_attempts['self_reflect'], 1)
        self.assertEqual(state['reflection_decision'].decision, 'rewrite_answer')
        self.assertEqual(state['result_contract'].kind, 'corrective_rewrite_applied')
        self.assertIsNotNone(state['result'].result_artifact)
        self.assertEqual(state['result_contract'].result_artifact_id, state['result'].result_artifact.artifact_id)
        self.assertEqual(
            [item.step_id for item in state['task_run'].checkpoints],
            ['check_guardrails', 'retrieve_evidence', 'self_reflect'],
        )
        trace_events = [event.name for event in self.trace.events]
        self.assertIn('workflow_step_started', trace_events)
        self.assertIn('workflow_step_completed', trace_events)
        self.assertIn('query_checkpoint_created', trace_events)

    def test_query_workflow_can_replay_from_self_reflect_checkpoint(self) -> None:
        orchestrator = self._build_orchestrator()

        state = orchestrator._invoke_workflow(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            ),
            mode='query',
        )
        checkpoint = next(item for item in state['task_run'].checkpoints if item.step_id == 'self_reflect')

        replayed = orchestrator.replay_from_checkpoint(checkpoint)

        self.assertEqual(replayed['result'].answer, state['result'].answer)
        self.assertEqual(replayed['replayed_from_checkpoint_id'], checkpoint.checkpoint_id)
        self.assertTrue(any(event.name == 'workflow_replayed' for event in self.trace.events))

    def test_query_run_history_is_queryable_and_persisted(self) -> None:
        orchestrator = self._build_orchestrator()

        state = orchestrator._invoke_workflow(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            ),
            mode='query',
        )

        summaries = orchestrator.list_query_runs()
        detail = orchestrator.get_query_run(state['task_run'].run_id)
        persisted = self.persistence.get_query_run(state['task_run'].run_id)

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].run_id, state['task_run'].run_id)
        self.assertGreaterEqual(summaries[0].event_count, 1)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.task_run.run_id, state['task_run'].run_id)
        self.assertEqual(detail.checkpoints[-1].step_id, 'self_reflect')
        self.assertEqual(detail.result_artifact_type, 'query_answer_artifact')
        self.assertTrue(any(item.name == 'query_completed' for item in detail.run_events))
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted['run_id'], state['task_run'].run_id)

    def test_query_run_replay_api_uses_persisted_checkpoint(self) -> None:
        orchestrator = self._build_orchestrator()

        state = orchestrator._invoke_workflow(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            ),
            mode='query',
        )

        replayed = orchestrator.replay_query_run(state['task_run'].run_id, checkpoint_id=state['task_run'].checkpoints[-1].checkpoint_id)

        self.assertNotEqual(replayed.run_id, state['task_run'].run_id)
        self.assertEqual(replayed.result.answer, state['result'].answer)
        self.assertEqual(replayed.replayed_from_checkpoint_id, state['task_run'].checkpoints[-1].checkpoint_id)

    def test_query_run_supports_resume_recover_and_analytics(self) -> None:
        orchestrator = self._build_orchestrator()

        state = orchestrator._invoke_workflow(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            ),
            mode='query',
        )
        record = self.state.query_runs[state['task_run'].run_id]
        record['status'] = 'failed'
        record['completed_at'] = None
        record['recoverable'] = True
        self.persistence.upsert_query_run(record)

        filtered = orchestrator.list_query_runs(recoverable_only=True, collection_name='demo')
        analytics = orchestrator.get_query_run_analytics(collection_name='demo')
        recovered = orchestrator.recover_query_runs(limit=10, auto_resume=True)

        self.assertEqual(len(filtered), 1)
        self.assertTrue(filtered[0].recoverable)
        self.assertEqual(analytics.total_runs, 1)
        self.assertEqual(analytics.recoverable_runs, 1)
        self.assertIn('query', analytics.mode_counts)
        self.assertIn('corrective_rewrite_applied', analytics.answer_mode_counts)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].result.answer, state['result'].answer)
        self.assertFalse(self.state.query_runs[state['task_run'].run_id]['recoverable'])

    def test_orchestration_node_contract_disallows_step_runtime_writes(self) -> None:
        orchestrator = self._build_orchestrator()
        nodes = QueryWorkflowNodes(orchestrator.classic_engine, self.trace)

        with self.assertRaisesRegex(RuntimeError, 'orchestration node'):
            nodes.validate_node_contract('blocked_response', {'completed_step_ids': ['check_guardrails']})

    def test_query_workflow_nodes_accept_runtime_adapter(self) -> None:
        orchestrator = self._build_orchestrator()
        runtime = ensure_query_workflow_runtime(orchestrator.classic_engine)
        nodes = QueryWorkflowNodes(runtime, self.trace)

        self.assertIs(nodes.runtime, runtime)

    def test_stream_query_keeps_sse_contract_on_langgraph_path(self) -> None:
        orchestrator = self._build_orchestrator()

        events = list(
            orchestrator.stream_query(
                QueryRequest(
                    question='session summary 接口是什�?,
                    collection_name='demo',
                    use_corrective_rag=True,
                    use_query_rewrite=False,
                )
            )
        )

        names = [item['event'] for item in events]
        self.assertEqual(names[0], 'start')
        self.assertIn('step_started', names)
        self.assertIn('step_completed', names)
        self.assertIn('checkpoint_created', names)
        self.assertIn('retrieval', names)
        self.assertIn('citation_ready', names)
        self.assertIn('answer_started', names)
        self.assertIn('corrective_check', names)
        self.assertEqual(names[-2], 'answer_completed')
        self.assertEqual(names[-1], 'done')
        self.assertEqual(events[-1]['data']['response']['answer'], 'session summary 接口用于压缩历史消息，并生成会话摘要�?)

    def test_query_without_corrective_rag_still_runs_on_graph(self) -> None:
        orchestrator = self._build_orchestrator()

        response = orchestrator.query(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=False,
                use_query_rewrite=False,
            )
        )

        self.assertIn('session summary 接口还会自动同步外部 CRM 数据�?, response.answer)
        workflow_nodes = [event.payload.get('node') for event in self.trace.events if event.name == 'workflow_node_completed']
        self.assertIn('retrieve_evidence', workflow_nodes)
        self.assertNotIn('execute_classic', workflow_nodes)
        self.assertFalse(any(event.name == 'self_rag_decision' for event in self.trace.events))

    def test_stream_query_without_corrective_rag_omits_corrective_event(self) -> None:
        orchestrator = self._build_orchestrator()

        events = list(
            orchestrator.stream_query(
                QueryRequest(
                    question='session summary 接口是什�?,
                    collection_name='demo',
                    use_corrective_rag=False,
                    use_query_rewrite=False,
                )
            )
        )

        names = [item['event'] for item in events]
        self.assertEqual(names[0], 'start')
        self.assertIn('retrieval', names)
        self.assertIn('citation_ready', names)
        self.assertIn('answer_started', names)
        self.assertNotIn('corrective_check', names)
        self.assertEqual(names[-2], 'answer_completed')
        self.assertEqual(names[-1], 'done')

    def test_query_self_rag_retry_retrieves_once_more_when_enabled(self) -> None:
        self.settings = Settings(
            DATA_DIR=Path(tempfile.mkdtemp()),
            QUERY_ORCHESTRATOR='langgraph',
            ENABLE_SELF_RAG_RETRY=True,
            SELF_RAG_MAX_RETRY_COUNT=1,
            SELF_RAG_MIN_GROUNDING_CONFIDENCE=0.65,
        )
        self.trace = TraceRecorder()
        self.state = InMemoryState()
        self.retrieval = FakeCorrectiveRetrievalService()
        self.fake_llm = FakeCorrectiveLLM()
        orchestrator = self._build_orchestrator()

        response = orchestrator.query(
            QueryRequest(
                question='session summary 接口是什�?,
                collection_name='demo',
                use_corrective_rag=True,
                use_query_rewrite=False,
            )
        )

        self.assertEqual(response.answer, 'session summary 接口用于压缩历史消息，并生成会话摘要�?)
        self.assertEqual(len(self.retrieval.calls), 2)
        self.assertTrue(any('请优先返回能直接支撑答案的事�? in item for item in self.retrieval.calls))
        decisions = [event.payload for event in self.trace.events if event.name == 'self_rag_decision']
        self.assertTrue(any(item.get('decision') == 'retry_retrieve' for item in decisions))
        workflow_nodes = [event.payload.get('node') for event in self.trace.events if event.name == 'workflow_node_completed']
        self.assertIn('retry_retrieve', workflow_nodes)


if __name__ == '__main__':
    unittest.main()

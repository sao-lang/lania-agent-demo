"""验证 SQLite 持久化层能够恢复各类状态桶。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class SQLitePersistenceTests(unittest.TestCase):
    """覆盖状态存储恢复到内存态的完整路径。"""

    def setUp(self) -> None:
        """创建独立的 SQLite 状态存储实例。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.store = SQLiteStateStore(self.settings)

    def test_load_into_restores_all_supported_state_buckets(self) -> None:
        """验证集合、文档、会话、评测、缓存与运行记录都能被恢复。"""
        now = datetime.now(timezone.utc)
        self.store.upsert_collection(
            {
                'id': 'col-1',
                'name': 'demo',
                'description': 'demo collection',
                'status': 'created',
                'embedding_model': 'text-embedding-3-small',
                'chunk_size': 800,
                'chunk_overlap': 100,
                'created_at': now,
                'updated_at': now,
            }
        )
        self.store.upsert_document(
            {
                'doc_id': 'doc-1',
                'file_name': 'demo.md',
                'file_path': '/tmp/demo.md',
                'file_type': 'md',
                'collection_name': 'demo',
                'tags': ['intro'],
                'checksum': 'abc',
                'status': 'indexed',
                'chunk_ids': ['chunk-1'],
                'indexed_chunks': 1,
                'created_at': now,
                'updated_at': now,
                'indexed_at': now,
            }
        )
        self.store.upsert_session(
            'session-1',
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': '你好',
                        'created_at': now,
                    }
                ],
                'summary': '历史摘要',
                'summary_updated_at': now,
                'compressed_message_count': 1,
                'updated_at': now,
            },
        )
        self.store.upsert_feedback_item(
            {
                'feedback_id': 'fb-1',
                'feedback_type': 'upvote',
                'collection_name': 'demo',
                'question': 'q',
                'answer': 'a',
                'session_id': 'session-1',
                'correction': None,
                'note': 'ok',
                'citations': [],
                'metadata': {'source': 'unit-test'},
                'eval_candidate_created': True,
                'created_at': now,
            }
        )
        self.store.upsert_eval_candidate(
            {
                'candidate_id': 'cand-1',
                'feedback_id': 'fb-1',
                'collection_name': 'demo',
                'question': 'q',
                'reference': 'ref',
                'answer': 'a',
                'feedback_type': 'upvote',
                'citations': [],
                'note': None,
                'created_at': now,
            }
        )
        self.store.upsert_eval_task(
            {
                'task_id': 'eval-1',
                'status': 'completed',
                'summary': 'done',
                'dataset_path': '/tmp/dataset.json',
                'collection_name': 'demo',
                'sample_count': 1,
                'success_count': 1,
                'failed_count': 0,
                'metrics': {'faithfulness': 0.9},
                'result_path': '/tmp/result.json',
                'error': None,
                'started_at': now,
                'completed_at': now,
            }
        )
        self.store.upsert_semantic_cache_entry(
            {
                'cache_id': 'sc-1',
                'collection_name': 'demo',
                'mode': 'query',
                'question': '会话摘要是什么',
                'normalized_question': '会话摘要是什么',
                'question_embedding': [1.0, 0.0, 0.0],
                'context_signature': None,
                'filters': {'year': {'eq': 2026}},
                'filters_signature': 'filters-signature',
                'strategy_signature': 'strategy-signature',
                'answer': '会话摘要用于压缩历史消息。',
                'answer_mode': 'local_fallback',
                'citations': [],
                'source_doc_ids': ['doc-1'],
                'metadata': {'retrieval_questions': ['会话摘要是什么']},
                'hit_count': 2,
                'created_at': now,
                'updated_at': now,
                'last_hit_at': now,
            }
        )
        self.store.upsert_graph_node(
            {
                'node_id': 'gn-1',
                'collection_name': 'demo',
                'name': 'session summary',
                'normalized_name': 'session summary',
                'entity_type': 'concept',
                'aliases': ['summary'],
                'doc_ids': ['doc-1'],
                'mention_count': 2,
                'metadata': {'section_title': 'overview'},
                'created_at': now,
                'updated_at': now,
            }
        )
        self.store.upsert_graph_edge(
            {
                'edge_id': 'ge-1',
                'collection_name': 'demo',
                'doc_id': 'doc-1',
                'source_node_id': 'gn-1',
                'source_name': 'session summary',
                'target_node_id': 'gn-2',
                'target_name': 'chat session',
                'relation': 'related_to',
                'normalized_relation': 'related_to',
                'evidence_chunk_id': 'chunk-1',
                'evidence_text': 'session summary 与 chat session 相关。',
                'weight': 0.2,
                'metadata': {'file_name': 'demo.md'},
                'created_at': now,
                'updated_at': now,
            }
        )
        self.store.upsert_query_run(
            {
                'run_id': 'query-run-1',
                'status': 'completed',
                'mode': 'query',
                'task_type': 'grounded_query',
                'collection_name': 'demo',
                'request_payload': {'question': 'session summary 是什么', 'collection_name': 'demo'},
                'task_spec': {'task_type': 'grounded_query', 'objective': 'answer query', 'steps': [], 'run_budget': {'top_k': 5}},
                'task_run': {
                    'run_id': 'query-run-1',
                    'task_id': 'query-run-1',
                    'status': 'completed',
                    'current_step_id': 'finalize',
                    'completed_step_ids': ['check_guardrails'],
                    'step_attempts': {'check_guardrails': 1},
                    'budget': {'top_k': 5},
                    'step_specs': [],
                    'step_runtimes': {},
                    'checkpoints': [],
                    'last_reflection_decision': None,
                    'started_at': now,
                    'completed_at': now,
                },
                'checkpoints': [],
                'run_events': [{'event_id': 'revt-1', 'name': 'workflow_completed', 'timestamp': now, 'payload': {'status': 'ok'}}],
                'result_contract': {'kind': 'grounded_answer'},
                'reflection_decision': None,
                'result': {
                    'answer': 'session summary 用于压缩历史消息。',
                    'citations': [],
                    'retrieved_count': 0,
                    'latency_ms': 12,
                    'session_id': None,
                },
                'replayed_from_checkpoint_id': None,
                'created_at': now,
                'updated_at': now,
                'completed_at': now,
            }
        )
        self.store.upsert_task_run(
            {
                'run_id': 'task-run-1',
                'task_id': 'task-1',
                'status': 'completed',
                'task_type': 'document_analysis',
                'collection_name': 'demo',
                'request_payload': {'collection_name': 'demo', 'instructions': '总结风险点'},
                'task_spec': {'task_type': 'document_analysis', 'objective': 'analyze docs', 'steps': [], 'run_budget': {'top_k': 5}},
                'task_run': {
                    'run_id': 'task-run-1',
                    'task_id': 'task-1',
                    'status': 'completed',
                    'current_step_id': 'finalize',
                    'completed_step_ids': ['load_task', 'plan_task', 'finalize'],
                    'step_attempts': {'load_task': 1, 'finalize': 1},
                    'budget': {'top_k': 5},
                    'step_specs': [],
                    'step_runtimes': {},
                    'checkpoints': [],
                    'last_reflection_decision': None,
                    'started_at': now,
                    'completed_at': now,
                },
                'checkpoints': [],
                'run_events': [{'event_id': 'revt-task-1', 'name': 'workflow_completed', 'timestamp': now, 'payload': {'status': 'ok'}}],
                'context_bundles': {},
                'result_contract': {'kind': 'document_analysis_report', 'final_artifact_id': 'artifact-1'},
                'final_artifact_id': 'artifact-1',
                'replayed_from_checkpoint_id': None,
                'last_checkpoint_id': None,
                'latency_ms': 21,
                'recoverable': False,
                'created_at': now,
                'updated_at': now,
                'completed_at': now,
            }
        )

        restored = InMemoryState()
        self.store.load_into(restored)

        self.assertIn('demo', restored.collections)
        self.assertIn('doc-1', restored.documents)
        self.assertIn('session-1', restored.sessions)
        self.assertEqual(len(restored.feedback_items), 1)
        self.assertEqual(len(restored.eval_candidates), 1)
        self.assertIn('eval-1', restored.eval_tasks)
        self.assertIn('sc-1', restored.semantic_cache)
        self.assertIn('gn-1', restored.graph_nodes)
        self.assertIn('ge-1', restored.graph_edges)
        self.assertIn('task-run-1', restored.task_runs)
        self.assertIn('query-run-1', restored.query_runs)
        self.assertIsInstance(restored.collections['demo']['created_at'], datetime)
        self.assertIsInstance(restored.sessions['session-1']['messages'][0]['created_at'], datetime)
        self.assertEqual(restored.eval_tasks['eval-1']['metrics']['faithfulness'], 0.9)
        self.assertEqual(restored.semantic_cache['sc-1']['hit_count'], 2)
        self.assertEqual(restored.graph_edges['ge-1']['evidence_chunk_id'], 'chunk-1')
        self.assertEqual(restored.task_runs['task-run-1']['final_artifact_id'], 'artifact-1')
        self.assertEqual(restored.query_runs['query-run-1']['run_events'][0]['name'], 'workflow_completed')


if __name__ == '__main__':
    unittest.main()

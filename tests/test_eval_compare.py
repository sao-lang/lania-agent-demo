"""评测对比测试，验证策略比较、默认策略补齐和观测样本回放等能力。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.eval import (
    EvalStrategyConfig,
    EvalTaskResponse,
    RagasEvalRequest,
    RagasCompareRequest,
    RagasCompareResponse,
)
from app.models.query import CitationItem, QueryResponse
from app.rag.observability import TraceRecorder
from app.services.eval_service import EvalService
from app.services.state import InMemoryState


class EvalCompareTests(unittest.TestCase):
    """评测对比测试集合，验证多策略对比计算、接口层映射和默认策略选择。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.service = EvalService(self.settings, self.state, self.trace, query_service=object())

    def test_compare_tasks_builds_metric_deltas_and_result_file(self) -> None:
        """覆盖 `compare_tasks_builds_metric_deltas_and_result_file` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        captured = []

        def fake_create_task(payload):
            captured.append(payload)
            strategy_name = (
                'accuracy_full_stack'
                if payload.use_parent_chunk_retrieval and payload.use_question_oriented_index and payload.use_corrective_rag
                else 'rewrite_hybrid_rerank'
            )
            metrics = (
                {'faithfulness': 0.82, 'answer_relevancy': 0.79}
                if strategy_name == 'rewrite_hybrid_rerank'
                else {'faithfulness': 0.9, 'answer_relevancy': 0.86}
            )
            return EvalTaskResponse(
                task_id=f'eval-{strategy_name}',
                status='completed',
                summary='ok',
                dataset_path=payload.dataset_path,
                collection_name=payload.collection_name,
                sample_count=2,
                success_count=2,
                failed_count=0,
                metrics=metrics,
                completed_at=datetime.now(timezone.utc),
            )

        self.service.create_task = fake_create_task
        response = self.service.compare_tasks(
            RagasCompareRequest(
                dataset_path='/tmp/eval.json',
                collection_name='demo',
                strategies=[
                    EvalStrategyConfig(name='rewrite_hybrid_rerank', use_query_rewrite=True, use_hybrid_retrieval=True, use_rerank=True),
                    EvalStrategyConfig(
                        name='accuracy_full_stack',
                        use_query_rewrite=True,
                        use_hybrid_retrieval=True,
                        use_rerank=True,
                        use_parent_chunk_retrieval=True,
                        use_question_oriented_index=True,
                        use_corrective_rag=True,
                    ),
                ],
            )
        )

        self.assertEqual(response.baseline_name, 'rewrite_hybrid_rerank')
        self.assertEqual(response.metrics['faithfulness'].best_strategy, 'accuracy_full_stack')
        self.assertEqual(response.metrics['faithfulness'].deltas['rewrite_hybrid_rerank'], 0.0)
        self.assertEqual(response.metrics['faithfulness'].deltas['accuracy_full_stack'], 0.08)
        self.assertTrue(captured[1].use_parent_chunk_retrieval)
        self.assertTrue(captured[1].use_question_oriented_index)
        self.assertTrue(captured[1].use_corrective_rag)
        self.assertTrue(Path(response.result_path).exists())

    def test_compare_endpoint_returns_compare_response(self) -> None:
        """覆盖 `compare_endpoint_returns_compare_response` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        app = create_app()
        client = TestClient(app)
        container = app.state.container

        container.eval_service.compare_tasks = lambda payload: RagasCompareResponse(
            compare_id='cmp-test',
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            baseline_name='rewrite_only',
            summary='ok',
            strategies=[],
            metrics={},
            result_path='/tmp/cmp-test.json',
            completed_at=datetime.now(timezone.utc),
        )

        response = client.post(
            '/api/v1/eval/ragas/compare',
            json={
                'dataset_path': '/tmp/eval.json',
                'collection_name': 'demo',
                'strategies': [
                    {'name': 'rewrite_only', 'use_query_rewrite': True, 'use_hybrid_retrieval': False, 'use_rerank': False},
                    {'name': 'rewrite_hybrid', 'use_query_rewrite': True, 'use_hybrid_retrieval': True, 'use_rerank': False},
                ],
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['compare_id'], 'cmp-test')

    def test_feedback_compare_endpoint_uses_default_strategies_when_omitted(self) -> None:
        """覆盖 `feedback_compare_endpoint_uses_default_strategies_when_omitted` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        app = create_app()
        client = TestClient(app)
        container = app.state.container

        container.feedback_service.export_eval_dataset = lambda payload: type(
            'DatasetPayload',
            (),
            {
                'dataset_path': '/tmp/feedback.json',
                'candidate_count': 2,
                'collection_name': 'demo',
                'candidate_ids': ['cand-1', 'cand-2'],
            },
        )()

        captured = {}

        def fake_compare(payload):
            captured['strategy_names'] = [item.name for item in payload.strategies]
            return RagasCompareResponse(
                compare_id='cmp-feedback',
                dataset_path=payload.dataset_path,
                collection_name=payload.collection_name,
                baseline_name=payload.strategies[0].name,
                summary='ok',
                strategies=[],
                metrics={},
                result_path='/tmp/cmp-feedback.json',
                completed_at=datetime.now(timezone.utc),
            )

        container.eval_service.compare_tasks = fake_compare

        response = client.post(
            '/api/v1/feedback/eval-ragas/compare',
            json={'collection_name': 'demo', 'limit': 2},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['comparison']['compare_id'], 'cmp-feedback')
        self.assertEqual(
            captured['strategy_names'],
            [
                'rewrite_hybrid_rerank',
                'parent_chunk_stack',
                'question_oriented_stack',
                'corrective_stack',
                'accuracy_full_stack',
                'graph_1hop_stack',
                'graph_2hop_stack',
                'accuracy_graph_full_stack',
            ],
        )

    def test_default_compare_strategies_cover_accuracy_features(self) -> None:
        """覆盖 `default_compare_strategies_cover_accuracy_features` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        strategies = self.service.build_default_compare_strategies(top_k=6)
        names = [item.name for item in strategies]

        self.assertEqual(
            names,
            [
                'rewrite_hybrid_rerank',
                'parent_chunk_stack',
                'question_oriented_stack',
                'corrective_stack',
                'accuracy_full_stack',
                'graph_1hop_stack',
                'graph_2hop_stack',
                'accuracy_graph_full_stack',
            ],
        )
        self.assertEqual(strategies[-1].top_k, 6)
        self.assertTrue(strategies[-1].use_parent_chunk_retrieval)
        self.assertTrue(strategies[-1].use_question_oriented_index)
        self.assertTrue(strategies[-1].use_corrective_rag)
        self.assertTrue(strategies[-1].use_graph_rag)
        self.assertEqual(strategies[-1].graph_max_hops, 2)

    def test_replay_queries_captures_sample_observability(self) -> None:
        """覆盖 `replay_queries_captures_sample_observability` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        class FakeQueryService:
            def __init__(self, trace: TraceRecorder) -> None:
                self.trace = trace

            def query(self, payload):
                self.trace.record(
                    'retrieval',
                    {
                        'retrieval_mode': 'hybrid',
                        'rerank_mode': 'disabled',
                        'pre_rerank': [
                            {'chunk_id': 'c1', 'matched_via': ['content']},
                            {'chunk_id': 'c1', 'matched_via': ['title_summary']},
                        ],
                        'post_aggregate': [
                            {'chunk_id': 'c1', 'matched_via': ['content', 'title_summary']},
                        ],
                        'parent_chunk': {
                            'expanded': 1,
                            'parent_document_hits': 1,
                        },
                    },
                )
                self.trace.record(
                    'query_completed',
                    {
                        'answer_mode': 'local_fallback',
                        'use_context_compression': True,
                        'context_compression': {
                            'compressed_chunk_count': 1,
                            'compressed_char_count': 120,
                        },
                        'semantic_cache': {
                            'hit': False,
                            'match_type': None,
                        },
                    },
                )
                return QueryResponse(
                    answer='ok',
                    citations=[
                        CitationItem(
                            chunk_id='c1',
                            source='demo.md',
                            text='session summary 接口用于压缩历史消息。',
                            score=0.9,
                            index_kind='content',
                            matched_via=['content', 'title_summary'],
                            chunking_strategy_requested='semantic',
                            chunking_strategy_effective='semantic',
                            chunking_prepared=True,
                            source_segment_count=3,
                            context_scope='parent',
                            parent_chunk_id='p1',
                        )
                    ],
                    retrieved_count=1,
                    latency_ms=10,
                    session_id=None,
                )

        service = EvalService(self.settings, self.state, self.trace, query_service=FakeQueryService(self.trace))
        payload = RagasEvalRequest(dataset_path='/tmp/eval.json', collection_name='demo')
        replay_rows, replay_details = service._replay_queries(
            [
                {
                    'question': 'session summary 接口是什么',
                    'reference': '用于压缩历史消息。',
                    'collection_name': 'demo',
                    'top_k': 2,
                }
            ],
            payload,
        )

        self.assertEqual(len(replay_rows), 1)
        observability = replay_details[0]['observability']
        self.assertTrue(observability['any_semantic_prepared_hit'])
        self.assertEqual(observability['matched_via_union'], ['content', 'title_summary'])
        self.assertEqual(observability['retrieval']['multi_vector_hits'], 1)
        self.assertEqual(observability['retrieval']['parent_document_hits'], 1)
        self.assertEqual(observability['query']['compressed_chunk_count'], 1)

        summary = service._summarize_replay_observability(replay_details)
        self.assertEqual(summary['sample_count'], 1)
        self.assertEqual(summary['multi_vector_samples'], 1)
        self.assertEqual(summary['semantic_prepared_samples'], 1)
        self.assertEqual(summary['parent_document_hits'], 1)
        self.assertEqual(summary['matched_via_breakdown']['content'], 1)
        self.assertEqual(summary['matched_via_breakdown']['title_summary'], 1)


if __name__ == '__main__':
    unittest.main()

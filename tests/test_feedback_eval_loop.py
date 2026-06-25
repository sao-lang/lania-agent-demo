"""反馈驱动评测闭环测试，覆盖候选样本导出和基于反馈发起评测任务的流程。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.eval import EvalTaskResponse
from app.models.feedback import EvalCandidateItem, FeedbackEvalRunRequest
from app.rag.observability import TraceRecorder
from app.services.feedback_service import FeedbackService
from app.services.state import InMemoryState


class FeedbackEvalLoopTests(unittest.TestCase):
    """反馈评测闭环测试集合，确认反馈样本可导出并驱动后续评测任务。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.service = FeedbackService(self.state, self.settings, self.trace)

        self.state.eval_candidates.extend(
            [
                EvalCandidateItem(
                    candidate_id='cand-1',
                    feedback_id='fb-1',
                    collection_name='demo',
                    question='q1',
                    reference='r1',
                    answer='a1',
                    feedback_type='upvote',
                    created_at=datetime.now(timezone.utc),
                ).model_dump(mode='json'),
                EvalCandidateItem(
                    candidate_id='cand-2',
                    feedback_id='fb-2',
                    collection_name='demo',
                    bucket='custom',
                    question='q2',
                    reference='r2',
                    answer='a2',
                    feedback_type='correction',
                    created_at=datetime.now(timezone.utc),
                ).model_dump(mode='json'),
                EvalCandidateItem(
                    candidate_id='cand-3',
                    feedback_id='fb-3',
                    collection_name='other',
                    question='q3',
                    reference='r3',
                    answer='a3',
                    feedback_type='upvote',
                    created_at=datetime.now(timezone.utc),
                ).model_dump(mode='json'),
            ]
        )

    def test_export_feedback_eval_dataset_writes_filtered_json(self) -> None:
        """覆盖 `export_feedback_eval_dataset_writes_filtered_json` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.service.export_eval_dataset(
            FeedbackEvalRunRequest(
                collection_name='demo',
                limit=1,
                top_k=4,
                use_query_rewrite=False,
                use_hybrid_retrieval=True,
                use_rerank=False,
                dataset_name='demo-feedback',
            )
        )

        self.assertEqual(response.collection_name, 'demo')
        self.assertEqual(response.candidate_count, 1)
        path = Path(response.dataset_path)
        self.assertTrue(path.exists())
        rows = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(rows), 1)
        self.assertIn('bucket', rows[0])
        self.assertEqual(rows[0]['collection_name'], 'demo')
        self.assertEqual(rows[0]['top_k'], 4)
        self.assertFalse(rows[0]['use_query_rewrite'])
        self.assertTrue(rows[0]['use_hybrid_retrieval'])
        self.assertFalse(rows[0]['use_rerank'])

    def test_export_feedback_eval_dataset_requires_collection_for_mixed_candidates(self) -> None:
        """覆盖 `export_feedback_eval_dataset_requires_collection_for_mixed_candidates` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with self.assertRaises(Exception) as context:
            self.service.export_eval_dataset(FeedbackEvalRunRequest())

        self.assertEqual(getattr(context.exception, 'code', None), 'feedback_eval_collection_required')

    def test_feedback_eval_ragas_endpoint_returns_dataset_and_task(self) -> None:
        """覆盖 `feedback_eval_ragas_endpoint_returns_dataset_and_task` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        app = create_app()
        client = TestClient(app)
        container = app.state.container

        exported = self.service.export_eval_dataset(
            FeedbackEvalRunRequest(
                collection_name='demo',
                limit=1,
                dataset_name='endpoint-feedback',
            )
        )
        container.feedback_service.export_eval_dataset = lambda payload: exported
        container.eval_service.create_task = lambda payload: EvalTaskResponse(
            task_id='eval-test',
            status='completed',
            summary='ok',
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            sample_count=1,
            success_count=1,
            failed_count=0,
        )

        response = client.post(
            '/api/v1/feedback/eval-ragas',
            json={'collection_name': 'demo', 'limit': 1},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['dataset_path'], exported.dataset_path)
        self.assertEqual(body['candidate_count'], 1)
        self.assertEqual(body['collection_name'], 'demo')
        self.assertEqual(body['task']['task_id'], 'eval-test')


if __name__ == '__main__':
    unittest.main()

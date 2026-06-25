"""验证回放对比接口会按题目分桶统计结果。"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.query import CitationItem, QueryResponse


class ReplayCompareTests(unittest.TestCase):
    """覆盖回放对比接口的基础成功路径。"""

    def setUp(self) -> None:
        """准备一个包含多个 bucket 的最小评测数据集。"""
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.dataset_path = self.tmp_dir / 'dataset.json'
        self.dataset_path.write_text(
            json.dumps(
                [
                    {
                        'bucket': 'api',
                        'question': 'session summary 接口是什么',
                        'ground_truth': 'x',
                        'collection_name': 'demo',
                        'top_k': 2,
                    },
                    {
                        'bucket': 'faq',
                        'question': '如何导出评测集 JSON',
                        'ground_truth': 'y',
                        'collection_name': 'demo',
                        'top_k': 2,
                    },
                ],
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

    def test_replay_compare_endpoint_returns_bucket_stats(self) -> None:
        """验证接口会返回对比任务 ID、策略列表和按桶聚合的统计信息。"""
        app = create_app()
        client = TestClient(app)

        def fake_query(payload):
            if 'session summary' in payload.question:
                return QueryResponse(
                    answer='ok',
                    citations=[
                        CitationItem(
                            chunk_id='c1',
                            source='demo.md',
                            text='session summary 接口用于压缩历史消息。',
                            score=0.9,
                        )
                    ],
                    retrieved_count=1,
                    latency_ms=10,
                    session_id=None,
                )
            return QueryResponse(
                answer='no',
                citations=[],
                retrieved_count=0,
                latency_ms=12,
                session_id=None,
            )

        app.state.container.query_service.query = fake_query

        response = client.post(
            '/api/v1/eval/replay/compare',
            json={
                'dataset_path': str(self.dataset_path),
                'collection_name': 'demo',
                'strategies': [
                    {'name': 's1', 'top_k': 2, 'use_query_rewrite': True},
                    {'name': 's2', 'top_k': 2, 'use_query_rewrite': False},
                ],
            },
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload['compare_id'].startswith('replay-'))
        self.assertEqual(len(payload['strategies']), 2)
        self.assertIn('metrics', payload)
        buckets = payload['strategies'][0]['buckets']
        self.assertIn('api', buckets)
        self.assertIn('faq', buckets)
        self.assertEqual(buckets['api']['success_count'], 1)
        self.assertEqual(buckets['faq']['failed_count'], 1)


if __name__ == '__main__':
    unittest.main()

"""验证多重改写能力在查询编排中的启用效果。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState


class FakeMultiRewriteRetrievalService:
    """模拟支持多重改写与批量检索的检索服务。"""

    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()
        self.last_multi: dict | None = None

    def rewrite_query_info(self, question: str) -> dict:
        return {
            'original_query': question,
            'normalized_query': question.strip(),
            'rewritten_query': f'rewritten::{question.strip()}',
            'applied_rules': ['legacy_rewrite'],
            'expanded_terms': [],
            'changed': True,
        }

    def rewrite_multi_query_info(self, question: str, max_queries: int = 3) -> dict:
        return {
            'enabled': True,
            'query_count': max_queries,
            'queries': [question, 'variant-a', 'variant-b'][:max_queries],
            'strategies': [{'kind': 'rewrite_base', 'query': question[:200]}],
        }

    def retrieve(self, **kwargs):
        raise AssertionError('retrieve should not be called when multi-rewrite is enabled and retrieve_multi exists')

    def retrieve_multi(self, **kwargs):
        self.last_multi = kwargs
        return [
            CitationItem(
                chunk_id='c1',
                source='demo.md',
                text='session summary 接口用于压缩历史消息。',
                score=0.9,
            )
        ]


class MultiRewriteTests(unittest.TestCase):
    """覆盖 Multi-Rewrite 的同步与流式行为。"""

    def setUp(self) -> None:
        """初始化开启多重改写测试所需的依赖。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakeMultiRewriteRetrievalService()

    def test_query_uses_retrieve_multi_when_multi_rewrite_enabled(self) -> None:
        """验证同步查询开启多重改写后会使用批量检索接口。"""
        with patch('app.rag.query_engine.build_llm', return_value=None):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

        response = engine.query(
            QueryRequest(
                question='session summary 接口是什么',
                collection_name='demo',
                use_query_rewrite=True,
                use_multi_rewrite=True,
                multi_rewrite_count=3,
            )
        )
        self.assertEqual(response.retrieved_count, 1)
        self.assertIsNotNone(self.retrieval.last_multi)
        questions = self.retrieval.last_multi['questions']
        self.assertEqual(len(questions), 3)
        self.assertTrue(questions[0].startswith('rewritten::'))

    def test_stream_query_emits_multi_rewrite_event(self) -> None:
        """验证流式查询会产出 `multi_rewrite` 事件并随后进入检索阶段。"""
        with patch('app.rag.query_engine.build_llm', return_value=None):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

        events = list(
            engine.stream_query(
                QueryRequest(
                    question='session summary 接口是什么',
                    collection_name='demo',
                    use_query_rewrite=True,
                    use_multi_rewrite=True,
                    multi_rewrite_count=3,
                )
            )
        )
        names = [item['event'] for item in events]
        self.assertEqual(names[0], 'start')
        self.assertEqual(names[1], 'rewrite')
        self.assertIn('multi_rewrite', names)
        rewrite_index = names.index('multi_rewrite')
        self.assertEqual(names[rewrite_index + 1], 'retrieval')


if __name__ == '__main__':
    unittest.main()

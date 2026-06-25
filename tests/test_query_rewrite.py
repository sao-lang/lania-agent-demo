"""验证查询改写规则与改写追踪事件。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.rag.retrieval import RagRetrievalService
from app.services.state import InMemoryState


class FakeVectorStoreFactory:
    """提供最小向量库工厂桩对象，隔离真实索引依赖。"""

    def get_or_create_collection(self, name: str):
        raise NotImplementedError


class FakeRewriteRetrievalService:
    """模拟始终返回固定改写结果的检索服务。"""

    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()

    def rewrite_query_info(self, question: str) -> dict:
        return {
            'original_query': question,
            'normalized_query': question.strip(),
            'rewritten_query': 'session summary 接口 如何使用 会话摘要',
            'applied_rules': ['remove_fillers', 'expand_domain_terms'],
            'expanded_terms': ['会话摘要'],
            'changed': True,
        }

    def retrieve(self, **kwargs):
        return []


class QueryRewriteTests(unittest.TestCase):
    """覆盖查询改写的规则执行与查询引擎追踪行为。"""

    def setUp(self) -> None:
        """初始化测试改写逻辑所需的状态与追踪器。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()

    def test_rewrite_query_info_expands_domain_terms_and_removes_fillers(self) -> None:
        """验证改写会扩展领域术语并移除口语化填充词。"""
        with patch('app.rag.retrieval.build_embed_model', return_value=object()):
            service = RagRetrievalService(
                self.settings,
                self.state,
                FakeVectorStoreFactory(),
                self.trace,
            )

        info = service.rewrite_query_info('帮我看下 session summary 接口怎么用')

        self.assertEqual(info['original_query'], '帮我看下 session summary 接口怎么用')
        self.assertIn('remove_fillers', info['applied_rules'])
        self.assertIn('expand_domain_terms', info['applied_rules'])
        self.assertIn('会话摘要', info['rewritten_query'])
        self.assertIn('如何', info['rewritten_query'])
        self.assertNotIn('帮我', info['rewritten_query'])
        self.assertNotIn('看下', info['rewritten_query'])

    def test_rewrite_query_info_deduplicates_repeated_terms(self) -> None:
        """验证重复词项会在改写结果中被去重。"""
        with patch('app.rag.retrieval.build_embed_model', return_value=object()):
            service = RagRetrievalService(
                self.settings,
                self.state,
                FakeVectorStoreFactory(),
                self.trace,
            )

        info = service.rewrite_query_info('session summary summary 接口 接口')

        self.assertIn('deduplicate_terms', info['applied_rules'])
        self.assertEqual(info['rewritten_query'].split().count('summary'), 1)
        self.assertEqual(info['rewritten_query'].split().count('接口'), 1)

    def test_query_engine_records_query_rewrite_trace(self) -> None:
        """验证查询引擎会记录结构化的改写追踪事件。"""
        with patch('app.rag.query_engine.build_llm', return_value=None):
            engine = RagQueryEngine(
                self.settings,
                self.state,
                FakeRewriteRetrievalService(),
                self.trace,
            )

        response = engine.query(
            QueryRequest(
                question='帮我看下 session summary',
                collection_name='demo',
                use_query_rewrite=True,
            )
        )

        self.assertEqual(response.retrieved_count, 0)
        rewrite_events = [event for event in self.trace.events if event.name == 'query_rewritten']
        self.assertEqual(len(rewrite_events), 1)
        self.assertEqual(rewrite_events[0].payload['context'], 'query')
        self.assertEqual(rewrite_events[0].payload['rewritten_query'], 'session summary 接口 如何使用 会话摘要')


if __name__ == '__main__':
    unittest.main()

"""验证多查询扩展在查询与流式查询路径中的行为。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState


class FakeLLM:
    """模拟生成多查询改写结果的最小 LLM。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if '只输出 JSON 数组' in prompt and 'Multi-Query' not in prompt:
            return '["session summary 接口 参数", "sessions/{session_id}/summary 用法"]'
        return 'final answer'


class FakeMultiRetrievalService:
    """模拟同时支持单查询和多查询检索的检索服务。"""

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

    def retrieve(self, **kwargs):
        raise AssertionError('retrieve should not be called when multi-query is enabled and retrieve_multi exists')

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


class MultiQueryTests(unittest.TestCase):
    """覆盖 Multi-Query 开关对查询编排的影响。"""

    def setUp(self) -> None:
        """初始化查询引擎所需的状态、追踪器和假检索服务。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakeMultiRetrievalService()

    def test_query_uses_retrieve_multi_when_enabled(self) -> None:
        """验证同步查询在开启多查询后会走 `retrieve_multi` 分支。"""
        fake_llm = FakeLLM()
        with patch('app.rag.query_engine.build_llm', return_value=fake_llm):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

        response = engine.query(
            QueryRequest(
                question='session summary 接口是什么',
                collection_name='demo',
                use_query_rewrite=True,
                use_multi_query=True,
                multi_query_count=3,
            )
        )
        self.assertEqual(response.retrieved_count, 1)
        self.assertIsNotNone(self.retrieval.last_multi)
        questions = self.retrieval.last_multi['questions']
        self.assertEqual(len(questions), 3)
        self.assertTrue(questions[0].startswith('rewritten::'))

    def test_stream_query_emits_multi_query_event(self) -> None:
        """验证流式查询会在检索前发出 `multi_query` 事件。"""
        fake_llm = FakeLLM()
        with patch('app.rag.query_engine.build_llm', return_value=fake_llm):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

        events = list(
            engine.stream_query(
                QueryRequest(
                    question='session summary 接口是什么',
                    collection_name='demo',
                    use_query_rewrite=True,
                    use_multi_query=True,
                    multi_query_count=3,
                )
            )
        )
        names = [item['event'] for item in events]
        self.assertEqual(names[0], 'start')
        self.assertEqual(names[1], 'rewrite')
        self.assertIn('multi_query', names)
        multi_index = names.index('multi_query')
        self.assertEqual(names[multi_index + 1], 'retrieval')


if __name__ == '__main__':
    unittest.main()

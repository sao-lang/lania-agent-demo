"""上下文压缩能力测试，确认检索结果在进入提示词前会按预期压缩并记录观测信息。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState


class FakeCompressionRetrievalService:
    """测试桩 `FakeCompressionRetrievalService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()

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
        return [
            CitationItem(
                chunk_id='c1',
                source='alpha.md',
                text=(
                    '完全无关的铺垫内容。'
                    'session summary 接口用于压缩历史消息。'
                    '更多无关噪声。'
                ),
                score=0.92,
            ),
            CitationItem(
                chunk_id='c2',
                source='beta.md',
                text='另一个片段主要描述上传接口，与当前问题关系较弱。',
                score=0.61,
            ),
        ]


class FakeLLM:
    """测试桩 `FakeLLM`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return 'compressed answer'


class ContextCompressionTests(unittest.TestCase):
    """上下文压缩测试集合，确保压缩后的上下文仍能驱动后续问答链路。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(
            DATA_DIR=Path(tempfile.mkdtemp()),
            CONTEXT_COMPRESSION_MAX_CHUNKS=2,
            CONTEXT_COMPRESSION_MAX_SENTENCES=1,
            CONTEXT_COMPRESSION_MAX_CHARS=120,
        )
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakeCompressionRetrievalService()
        self.fake_llm = FakeLLM()

    def test_query_compresses_context_before_prompt_generation(self) -> None:
        """覆盖 `query_compresses_context_before_prompt_generation` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with patch('app.rag.query_engine.build_llm', return_value=self.fake_llm):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

        response = engine.query(
            QueryRequest(
                question='session summary 接口是什么',
                collection_name='demo',
            )
        )

        self.assertEqual(response.answer, 'compressed answer')
        self.assertEqual(len(self.fake_llm.prompts), 1)
        prompt = self.fake_llm.prompts[0]
        self.assertIn('session summary 接口用于压缩历史消息', prompt)
        self.assertNotIn('更多无关噪声', prompt)
        compression_events = [event for event in self.trace.events if event.name == 'context_compressed']
        self.assertEqual(len(compression_events), 1)
        self.assertLess(
            compression_events[0].payload['compressed_char_count'],
            compression_events[0].payload['original_char_count'],
        )


if __name__ == '__main__':
    unittest.main()

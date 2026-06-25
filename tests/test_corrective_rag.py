"""纠错式 RAG 流程测试，覆盖答案改写、纠错检查事件和流式输出行为。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState


class FakeCorrectiveRetrievalService:
    """测试桩 `FakeCorrectiveRetrievalService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
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
                source='demo.md',
                text='session summary 接口用于压缩历史消息，并生成会话摘要。',
                score=0.93,
            )
        ]


class FakeCorrectiveLLM:
    """测试桩 `FakeCorrectiveLLM`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if '你是 RAG 结果校验器' in prompt:
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
        if '你是一个严格保守的 RAG 助手' in prompt:
            return 'session summary 接口用于压缩历史消息，并生成会话摘要。'
        return 'session summary 接口还会自动同步外部 CRM 数据。'


class CorrectiveRagTests(unittest.TestCase):
    """纠错式 RAG 测试集合，验证纠错判断、改写回答与流式事件输出。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakeCorrectiveRetrievalService()
        self.fake_llm = FakeCorrectiveLLM()

    def _build_engine(self) -> RagQueryEngine:
        """封装当前测试反复使用的构造步骤，减少样板代码并突出断言重点。"""
        with patch('app.rag.query_engine.build_llm', return_value=self.fake_llm):
            return RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

    def test_query_corrective_rag_rewrites_unsupported_answer(self) -> None:
        """覆盖 `query_corrective_rag_rewrites_unsupported_answer` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()

        with patch.object(
            engine.answer_service,
            'maybe_apply_corrective_rag',
            side_effect=AssertionError('should use knowledge capability corrective path'),
        ):
            response = engine.query(
                QueryRequest(
                    question='session summary 接口是什么',
                    collection_name='demo',
                    use_corrective_rag=True,
                    use_query_rewrite=False,
                )
            )

        self.assertEqual(response.answer, 'session summary 接口用于压缩历史消息，并生成会话摘要。')
        corrective_events = [event for event in self.trace.events if event.name == 'corrective_rag_checked']
        self.assertEqual(len(corrective_events), 1)
        self.assertEqual(corrective_events[0].payload['result'], 'corrected')
        self.assertEqual(corrective_events[0].payload['final_mode'], 'corrective_llm_rewrite')

    def test_stream_query_emits_corrective_check_event(self) -> None:
        """覆盖 `stream_query_emits_corrective_check_event` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()

        with patch.object(
            engine.answer_service,
            'maybe_apply_corrective_rag',
            side_effect=AssertionError('should use knowledge capability corrective path'),
        ):
            events = list(
                engine.stream_query(
                    QueryRequest(
                        question='session summary 接口是什么',
                        collection_name='demo',
                        use_corrective_rag=True,
                        use_query_rewrite=False,
                    )
                )
            )

        names = [item['event'] for item in events]
        self.assertIn('corrective_check', names)
        check_event = next(item for item in events if item['event'] == 'corrective_check')
        self.assertTrue(check_event['data']['applied'])
        self.assertEqual(check_event['data']['final_mode'], 'corrective_llm_rewrite')
        final_response = events[-1]['data']['response']
        self.assertEqual(final_response['answer'], 'session summary 接口用于压缩历史消息，并生成会话摘要。')


if __name__ == '__main__':
    unittest.main()

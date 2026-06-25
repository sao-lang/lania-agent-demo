"""验证查询预处理与纠错式回答服务的关键分支。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.services.answer_service import AnswerService
from app.services.query_preprocess_service import QueryPreprocessService


class FakeRetrievalService:
    """模拟只提供查询改写能力的检索服务。"""

    def rewrite_query(self, question: str) -> str:
        return f'rewritten::{question}'


class FakeLLM:
    """模拟既能做校验也能做重写回答的 LLM。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if '你是 RAG 结果校验器' in prompt:
            return json.dumps(
                {
                    'supported': False,
                    'confidence': 0.18,
                    'risk': 'high',
                    'reason': 'unsupported_claim',
                    'rewrite_needed': True,
                },
                ensure_ascii=False,
            )
        if '你是一个严格保守的 RAG 助手' in prompt:
            return '依据文档，session summary 用于压缩历史消息。'
        return '默认回答'


class QueryServicesTests(unittest.TestCase):
    """覆盖查询预处理护栏检查与 Corrective RAG 改写逻辑。"""

    def setUp(self) -> None:
        """组装预处理服务与回答服务，供各用例复用。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.trace = TraceRecorder()
        self.retrieval = FakeRetrievalService()
        self.llm = FakeLLM()
        self.preprocess = QueryPreprocessService(self.settings, self.retrieval, self.trace, self.llm)
        self.answer_service = AnswerService(self.settings, self.trace, self.preprocess, self.llm)

    def test_preprocess_check_guardrails_redacts_question(self) -> None:
        """验证预处理阶段会对问题中的敏感邮箱执行脱敏。"""
        payload = QueryRequest(
            question='我的邮箱是 foo@example.com',
            collection_name='demo',
        )

        state = self.preprocess.check_guardrails(payload.question, payload, 'query')

        self.assertFalse(state['blocked'])
        self.assertTrue(state['pii_redaction_enabled'])
        self.assertTrue(state['question_redaction']['applied'])
        self.assertIn('[REDACTED_EMAIL]', state['sanitized_question'])

    def test_answer_service_corrective_rag_rewrites_unsupported_answer(self) -> None:
        """验证 Corrective RAG 会在回答缺乏证据支撑时触发重写。"""
        payload = QueryRequest(
            question='session summary 是什么',
            collection_name='demo',
            use_corrective_rag=True,
        )
        citations = [
            CitationItem(
                chunk_id='c1',
                source='demo.md',
                text='session summary 用于压缩历史消息。',
                score=0.9,
            )
        ]

        answer, answer_mode, info = self.answer_service.maybe_apply_corrective_rag(
            payload=payload,
            question=payload.question,
            answer='session summary 还会自动同步 CRM 数据。',
            answer_mode='llm_complete',
            citations=citations,
            collection_name=payload.collection_name,
        )

        self.assertEqual(answer, '依据文档，session summary 用于压缩历史消息。')
        self.assertEqual(answer_mode, 'corrective_llm_rewrite')
        self.assertTrue(info['applied'])
        self.assertEqual(info['final_mode'], 'corrective_llm_rewrite')


if __name__ == '__main__':
    unittest.main()

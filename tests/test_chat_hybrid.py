"""混合检索聊天链路测试，覆盖托管检索、直接检索回退以及引用信息拼装等行为。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.capabilities.knowledge import GroundedAnswerResult
from app.models.artifact import EvidenceItem, EvidencePack
from app.core.config import Settings
from app.models.query import ChatRequest, CitationItem, QueryResponse
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState


class FakeRetrievalService:
    """测试桩 `FakeRetrievalService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()

    def rewrite_query(self, question: str) -> str:
        return f'rewritten::{question}'

    def _matches_filters(self, metadata: dict, filters: dict | None) -> bool:
        return True

    def _metadata_text(self, metadata: dict, key: str) -> str | None:
        value = metadata.get(key)
        if value in (None, ''):
            return None
        return str(value).strip() or None

    def _format_citation_source(self, metadata: dict) -> str:
        base_source = self._metadata_text(metadata, 'source') or self._metadata_text(metadata, 'file_name') or 'unknown'
        archive = self._metadata_text(metadata, 'source_archive')
        member = self._metadata_text(metadata, 'archive_member_display_path') or self._metadata_text(metadata, 'archive_member_path')
        if archive and member:
            return f'{archive} :: {member}'
        if archive:
            return f'{archive} :: {base_source}'
        return base_source


class FakeIndex:
    """测试桩 `FakeIndex`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def as_query_engine(self, llm, similarity_top_k: int, filters=None):
        return object()


class FakeNode:
    """测试桩 `FakeNode`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, node_id: str, text: str, metadata: dict) -> None:
        self.node_id = node_id
        self._text = text
        self.metadata = metadata

    def get_content(self) -> str:
        return self._text


class FakeChatResponse:
    """测试桩 `FakeChatResponse`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, text: str) -> None:
        self.text = text
        self.source_nodes = []

    def __str__(self) -> str:
        return self.text


class FakeChatEngine:
    """测试桩 `FakeChatEngine`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, response: FakeChatResponse) -> None:
        self.response = response

    def chat(self, question: str) -> FakeChatResponse:
        return self.response


class FakeSourceNode:
    """测试桩 `FakeSourceNode`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, node_id: str, text: str, metadata: dict, score: float) -> None:
        self.node = FakeNode(node_id=node_id, text=text, metadata=metadata)
        self.score = score


class FakeKnowledgeCapability:
    """测试桩 `FakeKnowledgeCapability`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def retrieve_evidence(self, request, *, trace_context=None):
        return EvidencePack(
            task_id='',
            evidence_items=[
                EvidenceItem(
                    citation_id='c1',
                    source='demo.md',
                    chunk_id='lex-1',
                    text='hybrid citation',
                    support_score=0.8,
                )
            ],
            coverage_score=1.0,
            missing_aspects=[],
        )

    def grounded_answer(self, request, *, trace_context=None):
        return GroundedAnswerResult(
            answer='hybrid citation',
            evidence_pack=self.retrieve_evidence(request, trace_context=trace_context),
            citations=[],
            grounded=True,
        )

    def load_document_context(self, request):
        raise NotImplementedError


class ChatHybridTests(unittest.TestCase):
    """混合检索聊天测试集合，验证不同检索开关下的聊天路径选择与引用拼装。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakeRetrievalService()

    def _build_engine(self) -> RagQueryEngine:
        """封装当前测试反复使用的构造步骤，减少样板代码并突出断言重点。"""
        with patch('app.rag.query_engine.build_llm', return_value=object()):
            return RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

    def test_chat_uses_managed_retrieval_when_hybrid_enabled(self) -> None:
        """覆盖 `chat_uses_managed_retrieval_when_hybrid_enabled` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()
        payload = ChatRequest(
            question='session summary 怎么看',
            collection_name='demo',
            session_id='s1',
            use_hybrid_retrieval=True,
        )
        fake_response = QueryResponse(
            answer='managed answer',
            citations=[
                CitationItem(
                    chunk_id='lex-1',
                    source='demo.md',
                    text='hybrid citation',
                    score=0.8,
                )
            ],
            retrieved_count=1,
            latency_ms=12,
            session_id='s1',
        )

        with patch.object(engine, '_run_query', return_value=fake_response) as mock_run:
            response = engine.chat(payload)

        self.assertEqual(response.answer, 'managed answer')
        self.assertEqual(len(self.state.sessions['s1']['messages']), 2)
        self.assertEqual(self.trace.events[-1].payload['chat_mode'], 'hybrid')
        self.assertTrue(self.trace.events[-1].payload['use_hybrid_retrieval'])
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.kwargs['answer_question'], 'session summary 怎么看')
        self.assertEqual(
            mock_run.call_args.kwargs['retrieval_question'],
            'rewritten::session summary 怎么看',
        )

    def test_run_query_uses_knowledge_capability_before_direct_retrieval(self) -> None:
        """覆盖 `run_query_uses_knowledge_capability_before_direct_retrieval` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()
        engine.knowledge_capability = FakeKnowledgeCapability()
        payload = ChatRequest(
            question='session summary 怎么看',
            collection_name='demo',
            session_id='s1',
            use_hybrid_retrieval=True,
            use_query_rewrite=False,
        )

        with (
            patch.object(engine.retrieval_service, 'retrieve', side_effect=AssertionError('should not call direct retrieval')),
            patch.object(engine.answer_service, 'generate_answer_with_mode', return_value=('hybrid citation', 'knowledge_capability')),
        ):
            response = engine._run_query(
                payload,
                retrieval_question='session summary 怎么看',
                answer_question='session summary 怎么看',
            )

        self.assertEqual(response.answer, 'hybrid citation')
        self.assertEqual(response.retrieved_count, 1)

    def test_chat_keeps_llamaindex_path_when_hybrid_disabled(self) -> None:
        """覆盖 `chat_keeps_llamaindex_path_when_hybrid_disabled` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()
        payload = ChatRequest(
            question='正常多轮问题',
            collection_name='demo',
            session_id='s2',
            use_hybrid_retrieval=False,
            use_context_compression=False,
        )

        with (
            patch('app.rag.query_engine.build_vector_store', return_value=object()),
            patch('app.rag.query_engine.VectorStoreIndex.from_vector_store', return_value=FakeIndex()),
            patch('app.rag.query_engine.ChatMemoryBuffer.from_defaults', return_value=object()),
            patch(
                'app.rag.query_engine.CondenseQuestionChatEngine.from_defaults',
                return_value=FakeChatEngine(FakeChatResponse('llamaindex answer')),
            ),
            patch.object(engine, '_run_query') as mock_run,
        ):
            response = engine.chat(payload)

        self.assertEqual(response.answer, 'llamaindex answer')
        self.assertEqual(self.trace.events[-1].payload['chat_mode'], 'llamaindex_chat_engine')
        self.assertFalse(self.trace.events[-1].payload['use_hybrid_retrieval'])
        mock_run.assert_not_called()

    def test_chat_uses_managed_retrieval_when_parent_chunk_enabled(self) -> None:
        """覆盖 `chat_uses_managed_retrieval_when_parent_chunk_enabled` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()
        payload = ChatRequest(
            question='总结一下 Alpha 章节',
            collection_name='demo',
            session_id='s3',
            use_parent_chunk_retrieval=True,
            use_context_compression=False,
        )
        fake_response = QueryResponse(
            answer='parent chunk answer',
            citations=[
                CitationItem(
                    chunk_id='child-1',
                    child_chunk_id='child-1',
                    parent_chunk_id='parent-1',
                    context_scope='parent',
                    source='demo.md',
                    text='expanded parent context',
                    score=0.86,
                )
            ],
            retrieved_count=1,
            latency_ms=8,
            session_id='s3',
        )

        with patch.object(engine, '_run_query', return_value=fake_response) as mock_run:
            response = engine.chat(payload)

        self.assertEqual(response.answer, 'parent chunk answer')
        self.assertEqual(self.trace.events[-1].payload['chat_mode'], 'parent_chunk')
        mock_run.assert_called_once()

    def test_chat_uses_managed_retrieval_when_question_oriented_index_enabled(self) -> None:
        """覆盖 `chat_uses_managed_retrieval_when_question_oriented_index_enabled` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()
        payload = ChatRequest(
            question='这个接口平时怎么查看',
            collection_name='demo',
            session_id='s4',
            use_question_oriented_index=True,
            use_context_compression=False,
        )
        fake_response = QueryResponse(
            answer='question oriented answer',
            citations=[
                CitationItem(
                    chunk_id='child-2',
                    source='demo.md',
                    text='question hint matched content',
                    score=0.77,
                )
            ],
            retrieved_count=1,
            latency_ms=7,
            session_id='s4',
        )

        with patch.object(engine, '_run_query', return_value=fake_response) as mock_run:
            response = engine.chat(payload)

        self.assertEqual(response.answer, 'question oriented answer')
        self.assertEqual(self.trace.events[-1].payload['chat_mode'], 'question_oriented')
        mock_run.assert_called_once()

    def test_source_node_citations_include_archive_display_source(self) -> None:
        """覆盖 `source_node_citations_include_archive_display_source` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()
        citations = engine._citations_from_source_nodes(
            [
                FakeSourceNode(
                    node_id='zip-1',
                    text='zip citation text',
                    metadata={
                        'source': 'readme.md',
                        'file_name': 'readme.md',
                        'source_archive': 'bundle.zip',
                        'archive_member_path': 'docs/readme.md',
                        'archive_member_display_path': 'docs > readme.md',
                    },
                    score=0.88,
                )
            ],
            None,
        )

        self.assertEqual(citations[0].source, 'bundle.zip :: docs > readme.md')
        self.assertEqual(citations[0].source_archive, 'bundle.zip')
        self.assertEqual(citations[0].archive_member_display_path, 'docs > readme.md')


if __name__ == '__main__':
    unittest.main()

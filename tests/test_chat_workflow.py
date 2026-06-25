"""聊天工作流测试，验证会话状态更新、历史上下文拼接和流式消息收尾处理。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import ChatRequest, CitationItem
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState
from app.workflows.query_orchestrator import QueryWorkflowOrchestrator


class FakeChatRetrievalService:
    """测试桩 `FakeChatRetrievalService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()
        self.calls: list[str] = []

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
        self.calls.append(question)
        return [
            CitationItem(
                chunk_id='c1',
                source='chat.md',
                text='session summary 接口用于压缩历史消息，并生成会话摘要。',
                score=0.91,
            )
        ]


class ChatWorkflowTests(unittest.TestCase):
    """聊天工作流测试集合，关注会话写回、历史拼接与流式完成后的状态同步。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()), QUERY_ORCHESTRATOR='langgraph')
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakeChatRetrievalService()

    def _build_orchestrator(self) -> QueryWorkflowOrchestrator:
        """封装当前测试反复使用的构造步骤，减少样板代码并突出断言重点。"""
        with patch('app.rag.query_engine.build_llm', return_value=None):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)
        return QueryWorkflowOrchestrator(self.settings, engine, self.trace)

    def test_chat_updates_session_and_records_chat_trace(self) -> None:
        """覆盖 `chat_updates_session_and_records_chat_trace` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        orchestrator = self._build_orchestrator()

        response = orchestrator.chat(
            ChatRequest(
                question='session summary 接口是什么',
                collection_name='demo',
                session_id='chat-langgraph',
                use_query_rewrite=False,
            )
        )

        self.assertEqual(response.session_id, 'chat-langgraph')
        self.assertEqual(len(self.state.sessions['chat-langgraph']['messages']), 2)
        self.assertEqual(self.state.sessions['chat-langgraph']['messages'][0]['role'], 'user')
        self.assertEqual(self.state.sessions['chat-langgraph']['messages'][1]['role'], 'assistant')
        self.assertTrue(any(event.name == 'chat_completed' for event in self.trace.events))
        workflow_nodes = [event.payload.get('node') for event in self.trace.events if event.name == 'workflow_node_completed']
        self.assertIn('check_guardrails', workflow_nodes)
        self.assertIn('load_session_context', workflow_nodes)
        self.assertIn('retrieve_evidence', workflow_nodes)
        self.assertIn('persist_session', workflow_nodes)
        self.assertNotIn('load_request', workflow_nodes)
        self.assertNotIn('execute_classic', workflow_nodes)

    def test_chat_second_turn_uses_history_in_retrieval_question(self) -> None:
        """覆盖 `chat_second_turn_uses_history_in_retrieval_question` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        orchestrator = self._build_orchestrator()

        orchestrator.chat(
            ChatRequest(
                question='session summary 接口是什么',
                collection_name='demo',
                session_id='chat-history',
                use_query_rewrite=False,
            )
        )
        orchestrator.chat(
            ChatRequest(
                question='它的作用是什么',
                collection_name='demo',
                session_id='chat-history',
                use_query_rewrite=False,
            )
        )

        self.assertEqual(len(self.retrieval.calls), 2)
        self.assertIn('session summary 接口是什么', self.retrieval.calls[1])
        self.assertIn('它的作用是什么', self.retrieval.calls[1])

    def test_stream_chat_updates_session_after_done(self) -> None:
        """覆盖 `stream_chat_updates_session_after_done` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        orchestrator = self._build_orchestrator()

        events = list(
            orchestrator.stream_chat(
                ChatRequest(
                    question='继续说一下 summary',
                    collection_name='demo',
                    session_id='chat-stream',
                    use_query_rewrite=False,
                )
            )
        )

        self.assertEqual(events[0]['event'], 'start')
        self.assertEqual(events[-1]['event'], 'done')
        self.assertEqual(len(self.state.sessions['chat-stream']['messages']), 2)
        self.assertEqual(self.state.sessions['chat-stream']['messages'][0]['role'], 'user')
        self.assertEqual(self.state.sessions['chat-stream']['messages'][1]['role'], 'assistant')


if __name__ == '__main__':
    unittest.main()

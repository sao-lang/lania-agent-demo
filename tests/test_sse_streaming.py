"""验证查询流式输出的事件顺序、SSE 编码与会话写回。"""

import asyncio
import json
import tempfile
import time
import unittest
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.rag_system.knowledge import GroundedAnswerResult
from app.agent_platform.core.config import Settings
from app.agent_platform.models.artifact import EvidenceItem, EvidencePack
from app.rag_system.models.query import ChatRequest, CitationItem, QueryRequest
from app.agent_platform.observability.trace_recorder import TraceRecorder
from app.rag_system.query.engine import RagQueryEngine
from app.rag_system.store.state import RagState


# ── SSE 编码辅助（原 app.api.v1.endpoints.query 迁移至此）──

SSEEvent = dict[str, Any]
SSEEventResult = tuple[bool, Any | None]


async def _encode_sse(
    request: Any,
    events: Iterator[SSEEvent],
    heartbeat_interval: float = 5.0,
) -> AsyncIterator[str]:
    """把同步事件迭代器编码为 SSE 文本流。"""
    stream_id = f'stream-{uuid4().hex[:12]}'
    request_id = request.headers.get('x-request-id') or f'req-{uuid4().hex[:12]}'
    event_index = 0
    iterator = iter(events)
    pending_task: asyncio.Task[SSEEventResult] | None = None

    while True:
        if hasattr(request, 'is_disconnected') and await request.is_disconnected():
            if pending_task is not None:
                pending_task.cancel()
            break

        if pending_task is None:
            pending_task = asyncio.create_task(asyncio.to_thread(_next_sse_event, iterator))

        try:
            done, item = await asyncio.wait_for(asyncio.shield(pending_task), timeout=heartbeat_interval)
            pending_task = None
        except asyncio.TimeoutError:
            yield _format_sse('heartbeat', {'request_id': request_id, 'stream_id': stream_id})
            continue

        if done:
            break
        if item is None:
            break

        event_index += 1
        yield _format_sse(
            item.get('event', 'message'),
            _enrich_sse_data(item.get('data', {}), request_id, stream_id, event_index),
        )


def _next_sse_event(iterator: Iterator[SSEEvent]) -> SSEEventResult:
    try:
        return False, next(iterator)
    except StopIteration:
        return True, None


def _enrich_sse_data(data: dict, request_id: str, stream_id: str, event_id: int) -> dict:
    enriched = dict(data)
    enriched.setdefault('request_id', request_id)
    enriched.setdefault('stream_id', stream_id)
    enriched.setdefault('event_id', event_id)
    return enriched


def _format_sse(event: str, data: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


class FakeRetrievalService:
    """模拟可返回归档路径信息的检索服务�?""

    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()

    def rewrite_query(self, question: str) -> str:
        return f'rewritten::{question}'

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
                source='bundle.zip :: docs > demo.md',
                text='session summary 接口用于压缩历史消息�?,
                score=0.9,
                source_archive='bundle.zip',
                archive_member_path='docs/demo.md',
                archive_member_display_path='docs > demo.md',
            )
        ]


class SSEStreamingTests(unittest.TestCase):
    """覆盖流式查询、知识能力优先级�?SSE 编码行为�?""

    def setUp(self) -> None:
        """初始化流式测试所需的查询引擎依赖。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = RagState()
        self.trace = TraceRecorder()
        self.retrieval = FakeRetrievalService()

    class FakeRequest:
        """模拟 Starlette 请求对象，只保留 SSE 编码所需接口�?""

        def __init__(self, headers=None, disconnected: bool = False) -> None:
            self.headers = headers or {}
            self._disconnected = disconnected

        async def is_disconnected(self) -> bool:
            return self._disconnected

    def _build_engine(self) -> RagQueryEngine:
        """构造一个禁用真�?LLM 的查询引擎实例�?""
        with patch('app.rag_system.query.engine.build_llm', return_value=None):
            return RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

    class FakeKnowledgeCapability:
        """模拟知识能力，验证其优先于直接检索分支执行�?""

        def retrieve_evidence(self, request, *, trace_context=None):
            return EvidencePack(
                task_id='',
                evidence_items=[
                    EvidenceItem(
                        citation_id='c1',
                        source='bundle.zip :: docs > demo.md',
                        chunk_id='c1',
                        text='session summary 接口用于压缩历史消息�?,
                        support_score=0.9,
                    )
                ],
                coverage_score=1.0,
                missing_aspects=[],
            )

        def grounded_answer(self, request, *, trace_context=None):
            return GroundedAnswerResult(
                answer='session summary 接口用于压缩历史消息�?,
                evidence_pack=self.retrieve_evidence(request, trace_context=trace_context),
                citations=[],
                grounded=True,
            )

        def load_document_context(self, request):
            raise NotImplementedError

    def test_stream_query_emits_progress_events_before_answer(self) -> None:
        """验证流式查询会按预期顺序输出进度、引用和最终回答事件�?""
        engine = self._build_engine()
        events = list(
            engine.stream_query(
                QueryRequest(
                    question='session summary 接口是什�?,
                    collection_name='demo',
                    use_query_rewrite=True,
                )
            )
        )

        event_names = [item['event'] for item in events]
        self.assertEqual(event_names[0], 'start')
        self.assertEqual(event_names[1], 'rewrite')
        self.assertEqual(event_names[2], 'retrieval')
        self.assertEqual(event_names[3], 'citation_ready')
        self.assertEqual(event_names[4], 'answer_started')
        self.assertEqual(event_names[-2], 'answer_completed')
        self.assertEqual(event_names[-1], 'done')
        self.assertGreaterEqual(event_names.count('delta'), 1)
        self.assertEqual(events[0]['data']['mode'], 'query')
        self.assertTrue(events[0]['data']['use_query_rewrite'])
        self.assertTrue(events[0]['data']['use_context_compression'])
        self.assertEqual(events[1]['data']['rewritten_query'], 'rewritten::session summary 接口是什�?)
        self.assertEqual(events[2]['data']['retrieval_question'], 'rewritten::session summary 接口是什�?)
        self.assertTrue(events[2]['data']['context_compression']['enabled'])
        self.assertEqual(events[3]['data']['citations'][0]['chunk_id'], 'c1')
        self.assertEqual(events[3]['data']['citations'][0]['source_archive'], 'bundle.zip')
        self.assertEqual(events[3]['data']['citations'][0]['archive_member_display_path'], 'docs > demo.md')
        final_response = events[-1]['data']['response']
        streamed_answer = ''.join(item['data']['delta'] for item in events if item['event'] == 'delta')
        self.assertEqual(streamed_answer, final_response['answer'])
        self.assertEqual(final_response['retrieved_count'], 1)

    def test_query_uses_knowledge_capability_before_direct_retrieval(self) -> None:
        """验证同步查询在知识能力可用时不会退回直接检索�?""
        engine = self._build_engine()
        engine.knowledge_capability = self.FakeKnowledgeCapability()

        with patch.object(engine.retrieval_service, 'retrieve', side_effect=AssertionError('should not call direct retrieval')):
            response = engine.query(
                QueryRequest(
                    question='session summary 接口是什�?,
                    collection_name='demo',
                    use_query_rewrite=True,
                )
            )

        self.assertIn('session summary', response.answer)
        self.assertEqual(response.retrieved_count, 1)

    def test_stream_query_uses_grounded_answer_before_direct_retrieval(self) -> None:
        """验证流式查询优先采用知识能力给出�?grounded answer�?""
        engine = self._build_engine()
        engine.knowledge_capability = self.FakeKnowledgeCapability()

        with patch.object(engine.retrieval_service, 'retrieve', side_effect=AssertionError('should not call direct retrieval')):
            events = list(
                engine.stream_query(
                    QueryRequest(
                        question='session summary 接口是什�?,
                        collection_name='demo',
                        use_query_rewrite=False,
                    )
                )
            )

        event_names = [item['event'] for item in events]
        self.assertIn('citation_ready', event_names)
        answer_completed = next(item for item in events if item['event'] == 'answer_completed')
        self.assertEqual(answer_completed['data']['answer_mode'], 'knowledge_capability_grounded')
        final_response = events[-1]['data']['response']
        self.assertIn('session summary', final_response['answer'])

    def test_stream_chat_updates_session_after_done(self) -> None:
        """验证流式聊天完成后会把问答消息写回会话状态�?""
        engine = self._build_engine()
        events = list(
            engine.stream_chat(
                ChatRequest(
                    question='继续说一�?summary',
                    collection_name='demo',
                    session_id='sse-chat',
                    use_query_rewrite=True,
                    use_hybrid_retrieval=True,
                )
            )
        )

        self.assertEqual(events[0]['event'], 'start')
        self.assertEqual(events[1]['event'], 'rewrite')
        self.assertEqual(events[3]['event'], 'citation_ready')
        self.assertEqual(events[4]['event'], 'answer_started')
        self.assertEqual(events[-2]['event'], 'answer_completed')
        self.assertEqual(events[-1]['event'], 'done')
        self.assertEqual(len(self.state.sessions['sse-chat']['messages']), 2)
        self.assertEqual(self.state.sessions['sse-chat']['messages'][0]['role'], 'user')
        self.assertEqual(self.state.sessions['sse-chat']['messages'][1]['role'], 'assistant')

    def test_query_stream_endpoint_returns_sse(self) -> None:
        """验证 HTTP 流式接口会返回标准 `text/event-stream` 响应。"""
        app = create_app()
        client = TestClient(app)

        def fake_graph_stream(request, steps=None):
            yield {'event': 'start', 'data': {'mode': 'query_stream'}}
            yield {'event': 'rewrite', 'data': {'rewritten_query': 'rewritten::hi'}}
            yield {'event': 'citation_ready', 'data': {'citations': []}}
            yield {'event': 'answer_started', 'data': {'retrieved_count': 0}}
            yield {'event': 'answer_completed', 'data': {'answer_mode': 'local_fallback'}}
            yield {'event': 'done', 'data': {'response': {'answer': 'ok', 'citations': [], 'retrieved_count': 0, 'latency_ms': 1, 'session_id': None}}}

        app.state.rag_system_container.graph_stream = fake_graph_stream
        with client.stream(
            'POST',
            '/api/v1/rag/query/graph/stream',
            headers={'x-request-id': 'req-from-test'},
            json={'question': 'hi', 'collection_name': 'demo'},
        ) as response:
            body = ''.join(response.iter_text())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
        self.assertIn('event: start', body)
        self.assertIn('event: rewrite', body)
        self.assertIn('event: citation_ready', body)
        self.assertIn('event: answer_started', body)
        self.assertIn('event: answer_completed', body)
        self.assertIn('event: done', body)
        self.assertIn('"request_id": "req-from-test"', body)
        self.assertIn('"stream_id": "stream-', body)

    def test_encode_sse_emits_heartbeat_for_slow_stream(self) -> None:
        """验证慢流场景�?SSE 编码器会周期性发送心跳事件�?""
        def slow_events():
            yield {'event': 'start', 'data': {'mode': 'query'}}
            time.sleep(0.03)
            yield {'event': 'done', 'data': {'response': {'answer': 'ok'}}}

        async def collect():
            chunks = []
            async for chunk in _encode_sse(
                self.FakeRequest(headers={'x-request-id': 'req-heartbeat'}),
                slow_events(),
                heartbeat_interval=0.005,
            ):
                chunks.append(chunk)
            return ''.join(chunks)

        body = asyncio.run(collect())
        self.assertIn('event: heartbeat', body)
        self.assertIn('"request_id": "req-heartbeat"', body)


if __name__ == '__main__':
    unittest.main()

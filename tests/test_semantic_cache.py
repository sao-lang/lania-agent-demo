"""验证语义缓存命中、流式事件与失效逻辑。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.document import ReindexRequest
from app.models.query import CitationItem, QueryRequest
from app.rag.llamaindex_components import HashEmbedding
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.document_service import DocumentService
from app.services.semantic_cache import SemanticCacheService
from app.services.state import InMemoryState


class FakeRetrievalService:
    """模拟可被语义缓存复用的检索服务。"""

    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = HashEmbedding(embed_dim=64)
        self.retrieve_calls = 0

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
        use_parent_chunk_retrieval: bool = False,
        use_question_oriented_index: bool = False,
    ) -> list[CitationItem]:
        self.retrieve_calls += 1
        return [
            CitationItem(
                chunk_id='doc-1-segment-0001',
                source='demo.md',
                file_path='/tmp/demo.md',
                text='会话摘要接口用于压缩多轮对话历史消息。',
                score=0.93,
            )
        ]


class FakeIngestionService:
    """模拟重建索引流程，仅用于触发缓存失效。"""

    def ensure_data_dirs(self) -> None:
        return None

    def reindex_documents(self, doc_ids: list[str]) -> dict[str, object]:
        return {'status': 'ok', 'doc_ids': doc_ids}


class SemanticCacheTests(unittest.TestCase):
    """覆盖语义缓存的查询命中、流式展示和重建失效。"""

    def setUp(self) -> None:
        """初始化开启语义缓存的查询环境。"""
        self.settings = Settings(
            DATA_DIR=Path(tempfile.mkdtemp()),
            ENABLE_SEMANTIC_CACHE=True,
            SEMANTIC_CACHE_SIMILARITY_THRESHOLD=0.8,
            SEMANTIC_CACHE_MIN_QUERY_LENGTH=2,
        )
        self.state = InMemoryState()
        self.state.collections['demo'] = {
            'id': 'col-1',
            'name': 'demo',
            'description': None,
            'status': 'created',
            'embedding_model': 'local-hash',
            'chunk_size': 800,
            'chunk_overlap': 100,
            'created_at': None,
            'updated_at': None,
        }
        self.trace = TraceRecorder()
        self.retrieval = FakeRetrievalService()
        self.semantic_cache = SemanticCacheService(
            self.settings,
            self.state,
            self.retrieval.embed_model,
            self.trace,
        )

    def _build_engine(self) -> RagQueryEngine:
        """构造一个挂接语义缓存服务的查询引擎。"""
        with patch('app.rag.query_engine.build_llm', return_value=None):
            return RagQueryEngine(
                self.settings,
                self.state,
                self.retrieval,
                self.trace,
                semantic_cache=self.semantic_cache,
            )

    def test_query_reuses_semantic_cache_for_semantically_similar_question(self) -> None:
        """验证语义相近的问题会复用已有缓存而不是再次检索。"""
        engine = self._build_engine()

        first = engine.query(QueryRequest(question='会话摘要是什么', collection_name='demo'))
        second = engine.query(QueryRequest(question='什么是会话摘要', collection_name='demo'))

        self.assertEqual(first.answer, second.answer)
        self.assertEqual(self.retrieval.retrieve_calls, 1)
        lookup_events = [event.payload for event in self.trace.events if event.name == 'semantic_cache_lookup']
        self.assertEqual(lookup_events[0]['reason'], 'no_candidates')
        self.assertTrue(lookup_events[-1]['hit'])
        self.assertEqual(lookup_events[-1]['match_type'], 'semantic')

    def test_stream_query_emits_cache_hit_event_after_cache_warmup(self) -> None:
        """验证流式查询在缓存预热后会显式发出命中事件。"""
        engine = self._build_engine()
        engine.query(QueryRequest(question='会话摘要是什么', collection_name='demo'))

        events = list(engine.stream_query(QueryRequest(question='什么是会话摘要', collection_name='demo')))

        self.assertEqual(self.retrieval.retrieve_calls, 1)
        event_names = [event['event'] for event in events]
        self.assertIn('cache_hit', event_names)
        self.assertEqual(events[-1]['data']['response']['retrieved_count'], 1)

    def test_reindex_invalidates_collection_cache(self) -> None:
        """验证文档重建索引会清理对应集合的语义缓存。"""
        ingestion = FakeIngestionService()
        document_service = DocumentService(
            self.settings,
            self.state,
            ingestion,
            semantic_cache=self.semantic_cache,
        )
        self.state.documents['doc-1'] = {
            'doc_id': 'doc-1',
            'file_name': 'demo.md',
            'file_path': '/tmp/demo.md',
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': [],
            'checksum': 'abc',
            'status': 'indexed',
            'chunk_ids': ['doc-1-segment-0001'],
            'indexed_chunks': 1,
            'created_at': None,
            'updated_at': None,
            'indexed_at': None,
        }
        self.semantic_cache.store(
            collection_name='demo',
            mode='query',
            question='会话摘要是什么',
            filters=None,
            strategy_signature='strategy',
            context_signature=None,
            answer='会话摘要接口用于压缩多轮对话历史消息。',
            answer_mode='local_fallback',
            citations=[],
            source_doc_ids=['doc-1'],
            metadata={},
        )

        result = document_service.reindex(ReindexRequest(collection_name='demo'))

        self.assertEqual(result['status'], 'ok')
        self.assertFalse(self.state.semantic_cache)


if __name__ == '__main__':
    unittest.main()

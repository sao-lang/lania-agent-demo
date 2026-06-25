"""文档权限过滤测试，验证权限范围展开、过滤条件求交以及聊天链路中的权限生效。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import ChatRequest, CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.query_engine import RagQueryEngine
from app.services.state import InMemoryState


class FakePermissionRetrievalService:
    """测试桩 `FakePermissionRetrievalService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.vector_store = object()
        self.embed_model = object()
        self.last_filters = None

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
        self.last_filters = filters
        return [
            CitationItem(
                chunk_id='c1',
                source='demo.md',
                text='public document',
                score=0.9,
            )
        ]

    def _matches_filters(self, metadata: dict, filters: dict | None) -> bool:
        if not filters:
            return True
        permission = metadata.get('permission')
        if permission is None:
            return False
        normalized = self._normalize_permission(permission)
        if normalized is None:
            return False
        permission_filter = filters.get('permission')
        if isinstance(permission_filter, dict) and 'in' in permission_filter:
            return normalized in {self._normalize_permission(item) for item in permission_filter['in']}
        return True

    def _normalize_permission(self, value) -> str | None:
        if value is None:
            return None
        alias_map = {
            'public': 'public',
            '公开': 'public',
            'internal': 'internal',
            '内部': 'internal',
            'private': 'private',
            '私有': 'private',
            'restricted': 'restricted',
            '受限': 'restricted',
            'confidential': 'confidential',
            '机密': 'confidential',
        }
        return alias_map.get(str(value).strip().lower(), str(value).strip().lower())


class FakeIndex:
    """测试桩 `FakeIndex`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def as_query_engine(self, llm, similarity_top_k: int, filters=None):
        self.filters = filters
        return object()


class FakeChatResponse:
    """测试桩 `FakeChatResponse`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.source_nodes = []

    def __str__(self) -> str:
        return 'llamaindex answer'


class FakeChatEngine:
    """测试桩 `FakeChatEngine`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def chat(self, question: str) -> FakeChatResponse:
        return FakeChatResponse()


class DocumentPermissionFilterTests(unittest.TestCase):
    """文档权限过滤测试集合，确认权限标签在检索和聊天阶段都被正确执行。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.retrieval = FakePermissionRetrievalService()

    def _build_engine(self) -> RagQueryEngine:
        """封装当前测试反复使用的构造步骤，减少样板代码并突出断言重点。"""
        with patch('app.rag.query_engine.build_llm', return_value=None):
            return RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

    def test_query_permission_scope_expands_to_allowed_permissions(self) -> None:
        """覆盖 `query_permission_scope_expands_to_allowed_permissions` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()

        response = engine.query(
            QueryRequest(
                question='公开资料有什么',
                collection_name='demo',
                use_query_rewrite=False,
                permission_scope='internal',
            )
        )

        self.assertEqual(response.retrieved_count, 1)
        self.assertEqual(
            self.retrieval.last_filters,
            {'permission': {'in': ['public', 'internal']}},
        )

    def test_query_permission_boundary_intersects_with_existing_permission_filter(self) -> None:
        """覆盖 `query_permission_boundary_intersects_with_existing_permission_filter` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        engine = self._build_engine()

        filters = engine._effective_filters(
            QueryRequest(
                question='看内部文档',
                collection_name='demo',
                use_query_rewrite=False,
                allowed_permissions=['public', 'internal'],
                filters={'permission': {'in': ['internal', 'restricted']}, 'year': 2026},
            )
        )

        self.assertEqual(
            filters,
            {'permission': {'in': ['internal']}, 'year': 2026},
        )

    def test_chat_llamaindex_path_uses_effective_permission_filters(self) -> None:
        """覆盖 `chat_llamaindex_path_uses_effective_permission_filters` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        self.retrieval._matches_filters = lambda metadata, filters: True
        with patch('app.rag.query_engine.build_llm', return_value=object()):
            engine = RagQueryEngine(self.settings, self.state, self.retrieval, self.trace)

        fake_index = FakeIndex()
        with (
            patch('app.rag.query_engine.build_vector_store', return_value=object()),
            patch('app.rag.query_engine.VectorStoreIndex.from_vector_store', return_value=fake_index),
            patch('app.rag.query_engine.build_metadata_filters', side_effect=lambda filters: filters),
            patch('app.rag.query_engine.ChatMemoryBuffer.from_defaults', return_value=object()),
            patch('app.rag.query_engine.CondenseQuestionChatEngine.from_defaults', return_value=FakeChatEngine()),
        ):
            engine.chat(
                ChatRequest(
                    question='正常多轮问题',
                    collection_name='demo',
                    session_id='s1',
                    use_hybrid_retrieval=False,
                    use_context_compression=False,
                    allowed_permissions=['公开', '内部'],
                )
            )

        self.assertEqual(
            fake_index.filters,
            {'permission': {'in': ['public', 'internal']}},
        )


if __name__ == '__main__':
    unittest.main()

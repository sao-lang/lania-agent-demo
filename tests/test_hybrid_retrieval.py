"""混合检索测试，覆盖稠密召回、词法召回、父块扩展和图谱候选融合。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.models.query import CitationItem
from app.rag.observability import TraceRecorder
from app.rag.retrieval import RagRetrievalService
from app.services.state import InMemoryState


class FakeNode:
    """测试桩 `FakeNode`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, node_id: str, text: str, metadata: dict) -> None:
        self.node_id = node_id
        self._text = text
        self.metadata = metadata

    def get_content(self) -> str:
        return self._text


class FakeNodeWithScore:
    """测试桩 `FakeNodeWithScore`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, node_id: str, text: str, metadata: dict, score: float) -> None:
        self.node = FakeNode(node_id=node_id, text=text, metadata=metadata)
        self.score = score


class FakeRetriever:
    """测试桩 `FakeRetriever`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, nodes, filters=None) -> None:
        self.nodes = nodes
        self.filters = filters

    def retrieve(self, question: str):
        if self.filters is None:
            return self.nodes
        dumped = self.filters.model_dump(mode='json')
        allowed_index_kind = None
        for entry in dumped.get('filters', []):
            if entry.get('key') == 'index_kind':
                if entry.get('operator') == 'in':
                    allowed_index_kind = set(entry.get('value') or [])
                else:
                    allowed_index_kind = {entry.get('value')}
        if not allowed_index_kind:
            return self.nodes
        return [node for node in self.nodes if node.node.metadata.get('index_kind') in allowed_index_kind]


class FakeIndex:
    """测试桩 `FakeIndex`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, nodes) -> None:
        self.nodes = nodes

    def as_retriever(self, similarity_top_k: int, filters=None):
        return FakeRetriever(self.nodes, filters=filters)


class FakeCollection:
    """测试桩 `FakeCollection`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.payload = {
            'ids': ['lex-1', 'shared', 'title-1'],
            'documents': [
                'beta keyword exact match',
                'alpha beta dense shared',
                '文档标题：Shared\n章节标题：Shared\n摘要：alpha beta dense shared',
            ],
            'metadatas': [
                {
                    'source': 'lexical.txt',
                    'file_name': 'lexical.txt',
                    'index_kind': 'query_hint',
                    'retrieval_target_chunk_id': 'shared',
                    'retrieval_target_text': 'alpha beta dense shared',
                    'section_title': 'Shared',
                    'hierarchy_path': 'Shared',
                },
                {'source': 'shared.txt', 'file_name': 'shared.txt', 'index_kind': 'content', 'node_level': 'child'},
                {
                    'source': 'shared.txt',
                    'file_name': 'shared.txt',
                    'index_kind': 'title_summary',
                    'node_level': 'child_aux',
                    'retrieval_target_chunk_id': 'shared',
                    'retrieval_target_text': 'alpha beta dense shared',
                    'section_title': 'Shared',
                    'hierarchy_path': 'Shared',
                },
            ],
        }
        self.chunk_metadata = {
            'dense-1': {
                'source': 'dense.txt',
                'file_name': 'dense.txt',
                'parent_chunk_id': 'parent-c',
                'parent_context': '文档：Dense\n章节：Alpha\nalpha only from dense. alpha shared parent context.',
                'section_title': 'Alpha',
                'hierarchy_path': 'Alpha',
            },
            'shared': {
                'source': 'shared.txt',
                'file_name': 'shared.txt',
                'parent_chunk_id': 'parent-a',
                'parent_context': '文档：Dense\n章节：Alpha\nalpha beta dense shared. alpha shared parent context.',
                'section_title': 'Alpha',
                'hierarchy_path': 'Alpha',
            },
            'lex-1': {
                'source': 'lexical.txt',
                'file_name': 'lexical.txt',
                'parent_chunk_id': 'parent-b',
                'parent_context': '文档：Lexical\n章节：Beta\nbeta keyword exact match.',
                'section_title': 'Beta',
                'hierarchy_path': 'Beta',
            },
            'parent-a': {
                'source': 'shared.txt',
                'file_name': 'shared.txt',
                'index_kind': 'parent',
                'node_level': 'parent',
                'section_title': 'Alpha',
                'hierarchy_path': 'Alpha',
            },
            'parent-b': {
                'source': 'lexical.txt',
                'file_name': 'lexical.txt',
                'index_kind': 'parent',
                'node_level': 'parent',
                'section_title': 'Beta',
                'hierarchy_path': 'Beta',
            },
        }
        self.chunk_documents = {
            'parent-a': '文档：Dense\n章节：Alpha\n这是独立的父块文档内容。',
            'parent-b': '文档：Lexical\n章节：Beta\n这是词法命中的父块文档内容。',
        }

    def get(self, include, ids=None):
        if ids is None:
            return self.payload
        if 'documents' in include:
            return {
                'ids': ids,
                'documents': [self.chunk_documents.get(str(item), '') for item in ids],
                'metadatas': [self.chunk_metadata.get(str(item), {}) for item in ids],
            }
        return {
            'ids': ids,
            'metadatas': [self.chunk_metadata.get(str(item), {}) for item in ids],
        }


class FakeVectorStoreFactory:
    """测试桩 `FakeVectorStoreFactory`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self) -> None:
        self.collection = FakeCollection()

    def get_or_create_collection(self, name: str):
        return self.collection


class FakeGraphService:
    """测试桩 `FakeGraphService`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def retrieve(
        self,
        *,
        collection_name: str,
        question: str,
        top_k: int,
        max_hops: int = 1,
        filters=None,
        entity_types=None,
    ):
        _ = (collection_name, question, filters, entity_types)
        return (
            [
                CitationItem(
                    chunk_id='shared',
                    matched_chunk_id='shared',
                    source='shared.txt',
                    text='alpha beta dense shared',
                    score=0.95,
                    index_kind='graph',
                    node_level='graph_evidence',
                    matched_via=['graph_entity', 'graph_path'],
                    context_scope='graph_evidence',
                    graph_path=f'alpha --related_to--> beta ({max_hops} hop)',
                    graph_relation='related_to',
                    graph_start_entity='alpha',
                    graph_end_entity='beta',
                    graph_path_hops=max_hops,
                )
            ],
            {
                'enabled': True,
                'seed_node_count': 2,
                'expanded_edge_count': 1,
                'returned_citations': 1,
                'max_hops': max_hops,
            },
        )


class HybridRetrievalTests(unittest.TestCase):
    """混合检索测试集合，验证不同召回源与融合策略的输出稳定性。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.vector_store = FakeVectorStoreFactory()
        self.dense_nodes = [
            FakeNodeWithScore(
                node_id='dense-1',
                text='alpha only from dense',
                metadata={'source': 'dense.txt', 'file_name': 'dense.txt', 'index_kind': 'content'},
                score=0.9,
            ),
            FakeNodeWithScore(
                node_id='shared',
                text='alpha beta dense shared',
                metadata={'source': 'shared.txt', 'file_name': 'shared.txt', 'index_kind': 'content', 'node_level': 'child'},
                score=0.7,
            ),
            FakeNodeWithScore(
                node_id='hint-1',
                text='session summary 怎么看',
                metadata={
                    'source': 'shared.txt',
                    'file_name': 'shared.txt',
                    'index_kind': 'query_hint',
                    'retrieval_target_chunk_id': 'shared',
                    'retrieval_target_text': 'alpha beta dense shared',
                    'section_title': 'Shared',
                    'hierarchy_path': 'Shared',
                    'node_level': 'child_aux',
                },
                score=0.95,
            ),
            FakeNodeWithScore(
                node_id='title-1',
                text='文档标题：Shared\n章节标题：Shared\n摘要：alpha beta dense shared',
                metadata={
                    'source': 'shared.txt',
                    'file_name': 'shared.txt',
                    'index_kind': 'title_summary',
                    'node_level': 'child_aux',
                    'retrieval_target_chunk_id': 'shared',
                    'retrieval_target_text': 'alpha beta dense shared',
                    'section_title': 'Shared',
                    'hierarchy_path': 'Shared',
                },
                score=0.88,
            ),
        ]

    def _build_service(self) -> RagRetrievalService:
        """封装当前测试反复使用的构造步骤，减少样板代码并突出断言重点。"""
        with patch('app.rag.retrieval.build_embed_model', return_value=object()):
            return RagRetrievalService(self.settings, self.state, self.vector_store, self.trace)

    def test_dense_only_does_not_include_lexical_only_hits(self) -> None:
        """覆盖 `dense_only_does_not_include_lexical_only_hits` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        with (
            patch('app.rag.retrieval.build_vector_store', return_value=object()),
            patch(
                'app.rag.retrieval.VectorStoreIndex.from_vector_store',
                return_value=FakeIndex(self.dense_nodes),
            ),
        ):
            citations = service.retrieve(
                collection_name='demo',
                question='alpha beta',
                top_k=3,
                use_hybrid_retrieval=False,
                use_rerank=False,
            )

        chunk_ids = [item.chunk_id for item in citations]
        self.assertEqual(chunk_ids, ['dense-1', 'shared'])
        self.assertEqual(self.trace.events[-1].payload['retrieval_mode'], 'dense')
        self.assertEqual(self.trace.events[-1].payload['lexical_candidates'], 0)

    def test_hybrid_retrieval_fuses_dense_and_lexical_hits(self) -> None:
        """覆盖 `hybrid_retrieval_fuses_dense_and_lexical_hits` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        with (
            patch('app.rag.retrieval.build_vector_store', return_value=object()),
            patch(
                'app.rag.retrieval.VectorStoreIndex.from_vector_store',
                return_value=FakeIndex(self.dense_nodes),
            ),
        ):
            citations = service.retrieve(
                collection_name='demo',
                question='alpha beta',
                top_k=3,
                use_hybrid_retrieval=True,
                use_rerank=False,
            )

        chunk_ids = [item.chunk_id for item in citations]
        self.assertEqual(chunk_ids[0], 'shared')
        self.assertIn('dense-1', chunk_ids)
        self.assertEqual(self.trace.events[-1].payload['retrieval_mode'], 'hybrid')
        self.assertGreaterEqual(self.trace.events[-1].payload['lexical_candidates'], 1)

    def test_question_oriented_index_includes_query_hint_hits(self) -> None:
        """覆盖 `question_oriented_index_includes_query_hint_hits` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        with (
            patch('app.rag.retrieval.build_vector_store', return_value=object()),
            patch(
                'app.rag.retrieval.VectorStoreIndex.from_vector_store',
                return_value=FakeIndex(self.dense_nodes),
            ),
        ):
            citations = service.retrieve(
                collection_name='demo',
                question='session summary 怎么看',
                top_k=2,
                use_hybrid_retrieval=False,
                use_rerank=False,
                use_question_oriented_index=True,
            )

        self.assertTrue(citations)
        self.assertEqual(citations[0].chunk_id, 'shared')
        self.assertEqual(citations[0].text, 'alpha beta dense shared')
        self.assertEqual(sorted(citations[0].matched_via or []), ['content', 'query_hint', 'title_summary'])
        self.assertTrue(self.trace.events[-1].payload['use_question_oriented_index'])
        self.assertEqual(
            self.trace.events[-1].payload['effective_filters']['index_kind'],
            ['content', 'query_hint', 'title_summary'],
        )
        self.assertEqual(self.trace.events[-1].payload['post_aggregate'][0]['chunk_id'], 'shared')

    def test_parent_chunk_retrieval_expands_child_hits_to_parent_context(self) -> None:
        """覆盖 `parent_chunk_retrieval_expands_child_hits_to_parent_context` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        with (
            patch('app.rag.retrieval.build_vector_store', return_value=object()),
            patch(
                'app.rag.retrieval.VectorStoreIndex.from_vector_store',
                return_value=FakeIndex(self.dense_nodes),
            ),
        ):
            citations = service.retrieve(
                collection_name='demo',
                question='alpha beta',
                top_k=3,
                use_hybrid_retrieval=True,
                use_rerank=False,
                use_parent_chunk_retrieval=True,
            )

        self.assertEqual(len(citations), 2)
        self.assertEqual(citations[0].context_scope, 'parent')
        self.assertEqual(citations[0].parent_chunk_id, 'parent-a')
        self.assertEqual(citations[0].child_chunk_id, 'shared')
        self.assertIn('独立的父块文档内容', citations[0].text)
        self.assertTrue(self.trace.events[-1].payload['use_parent_chunk_retrieval'])
        self.assertEqual(self.trace.events[-1].payload['parent_chunk']['expanded'], 2)
        self.assertEqual(self.trace.events[-1].payload['parent_chunk']['source'], 'parent_documents')

    def test_archive_citation_source_is_rendered_with_archive_context(self) -> None:
        """覆盖 `archive_citation_source_is_rendered_with_archive_context` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        archive_nodes = [
            FakeNodeWithScore(
                node_id='zip-1',
                text='zip text',
                metadata={
                    'source': 'readme.md',
                    'file_name': 'readme.md',
                    'source_archive': 'bundle.zip',
                    'archive_member_display_path': 'docs > readme.md',
                    'index_kind': 'content',
                },
                score=0.91,
            )
        ]
        with (
            patch('app.rag.retrieval.build_vector_store', return_value=object()),
            patch(
                'app.rag.retrieval.VectorStoreIndex.from_vector_store',
                return_value=FakeIndex(archive_nodes),
            ),
        ):
            citations = service.retrieve(
                collection_name='demo',
                question='zip text',
                top_k=1,
                use_hybrid_retrieval=False,
                use_rerank=False,
            )

        self.assertEqual(citations[0].source, 'bundle.zip :: docs > readme.md')
        self.assertEqual(citations[0].source_archive, 'bundle.zip')
        self.assertEqual(citations[0].archive_member_display_path, 'docs > readme.md')

    def test_graph_rag_fuses_graph_candidates_into_ranked_results(self) -> None:
        """覆盖 `graph_rag_fuses_graph_candidates_into_ranked_results` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with patch('app.rag.retrieval.build_embed_model', return_value=object()):
            service = RagRetrievalService(
                self.settings,
                self.state,
                self.vector_store,
                self.trace,
                graph_service=FakeGraphService(),
            )
        with (
            patch('app.rag.retrieval.build_vector_store', return_value=object()),
            patch(
                'app.rag.retrieval.VectorStoreIndex.from_vector_store',
                return_value=FakeIndex(self.dense_nodes),
            ),
        ):
            citations = service.retrieve(
                collection_name='demo',
                question='alpha beta',
                top_k=3,
                use_hybrid_retrieval=True,
                use_graph_rag=True,
                graph_max_hops=2,
                use_rerank=False,
            )

        self.assertTrue(citations)
        self.assertEqual(citations[0].chunk_id, 'shared')
        self.assertIn('graph_entity', citations[0].matched_via or [])
        self.assertEqual(citations[0].graph_path_hops, 2)
        self.assertEqual(self.trace.events[-1].payload['retrieval_mode'], 'hybrid_graph')
        self.assertEqual(self.trace.events[-1].payload['graph_candidates'], 1)
        self.assertTrue(self.trace.events[-1].payload['use_graph_rag'])


if __name__ == '__main__':
    unittest.main()

"""图谱增强检索测试，验证文档图构建、图谱引用回填以及 LLM 回退策略。"""

import unittest
from datetime import datetime, timezone

from app.models.query import CitationItem
from app.services.graph_service import GraphService
from app.services.state import InMemoryState
from app.rag.observability import TraceRecorder


class FakeGraphCollection:
    """测试桩 `FakeGraphCollection`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def get(self, ids=None, include=None):
        _ = include
        if ids is None:
            ids = []
        return {
            'ids': ids,
            'documents': ['session summary 接口用于压缩历史消息，并生成会话摘要。' for _ in ids],
            'metadatas': [
                {
                    'source': 'demo.md',
                    'file_name': 'demo.md',
                    'file_path': '/tmp/demo.md',
                    'section_title': '会话摘要',
                    'hierarchy_path': '总览 > 会话摘要',
                }
                for _ in ids
            ],
        }


class FakeVectorStoreFactory:
    """测试桩 `FakeVectorStoreFactory`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def get_or_create_collection(self, name: str):
        _ = name
        return FakeGraphCollection()


class FakeLLMResponse:
    """测试桩 `FakeLLMResponse`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


class FakeLLM:
    """测试桩 `FakeLLM`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def __init__(self, text: str, *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail

    def complete(self, prompt: str) -> FakeLLMResponse:
        _ = prompt
        if self.fail:
            raise RuntimeError('llm unavailable')
        return FakeLLMResponse(self.text)


class GraphRagTests(unittest.TestCase):
    """图谱增强检索测试集合，验证图谱抽取、引用回填以及异常回退逻辑。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.service = GraphService(self.state, FakeVectorStoreFactory(), self.trace)

    def test_replace_document_graph_extracts_nodes_and_edges(self) -> None:
        """覆盖 `replace_document_graph_extracts_nodes_and_edges` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        now = datetime.now(timezone.utc)
        record = {
            'doc_id': 'doc-1',
            'file_name': 'demo.md',
            'file_path': '/tmp/demo.md',
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': ['guide'],
            'status': 'indexed',
            'chunk_ids': [],
            'indexed_chunks': 0,
            'created_at': now,
            'updated_at': now,
            'indexed_at': now,
            'document_title': '会话摘要说明',
            'document_keywords': ['session summary', 'chat session'],
            'document_summary': '会话摘要用于压缩上下文',
            'document_hierarchy': 'demo',
            'year': '2026',
            'quarter': 'Q2',
            'version': 'v1.0',
            'permission': 'internal',
        }
        segments = [
            {
                'text': 'session summary 接口关联 chat session，并用于压缩历史消息。',
                'section_title': '会话摘要',
                'hierarchy_path': '总览 > 会话摘要',
                'page': 1,
            }
        ]

        result = self.service.replace_document_graph(record, segments)

        self.assertGreaterEqual(result['nodes'], 4)
        self.assertGreaterEqual(result['edges'], 1)
        self.assertTrue(self.state.graph_nodes)
        self.assertTrue(self.state.graph_edges)
        node_names = {item['name'] for item in self.state.graph_nodes.values()}
        self.assertIn('session summary', node_names)
        self.assertIn('chat session', node_names)
        relations = {
            (item['source_name'], item['relation'], item['target_name'])
            for item in self.state.graph_edges.values()
        }
        self.assertIn(('session summary', 'related_to', 'chat session'), relations)
        self.assertFalse(result['llm_enabled'])

    def test_retrieve_returns_graph_backed_citations(self) -> None:
        """覆盖 `retrieve_returns_graph_backed_citations` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        now = datetime.now(timezone.utc)
        self.state.graph_nodes['gn-1'] = {
            'node_id': 'gn-1',
            'collection_name': 'demo',
            'name': 'session summary',
            'normalized_name': 'session summary',
            'entity_type': 'concept',
            'aliases': ['summary'],
            'doc_ids': ['doc-1'],
            'mention_count': 1,
            'metadata': {},
            'created_at': now,
            'updated_at': now,
        }
        self.state.graph_nodes['gn-2'] = {
            'node_id': 'gn-2',
            'collection_name': 'demo',
            'name': 'chat session',
            'normalized_name': 'chat session',
            'entity_type': 'concept',
            'aliases': ['session'],
            'doc_ids': ['doc-1'],
            'mention_count': 1,
            'metadata': {},
            'created_at': now,
            'updated_at': now,
        }
        self.state.graph_edges['ge-1'] = {
            'edge_id': 'ge-1',
            'collection_name': 'demo',
            'doc_id': 'doc-1',
            'source_node_id': 'gn-1',
            'source_name': 'session summary',
            'target_node_id': 'gn-2',
            'target_name': 'chat session',
            'relation': 'related_to',
            'normalized_relation': 'related_to',
            'evidence_chunk_id': 'doc-1-segment-0001',
            'evidence_text': 'session summary 接口关联 chat session。',
            'weight': 0.2,
            'metadata': {'permission': 'internal'},
            'created_at': now,
            'updated_at': now,
        }

        citations, info = self.service.retrieve(
            collection_name='demo',
            question='session summary 和 chat session 有什么关系',
            top_k=3,
            max_hops=2,
        )

        self.assertTrue(info['enabled'])
        self.assertEqual(info['seed_node_count'], 2)
        self.assertEqual(len(citations), 1)
        self.assertIsInstance(citations[0], CitationItem)
        self.assertIn('session summary', citations[0].graph_path or '')
        self.assertEqual(citations[0].index_kind, 'graph')

    def test_replace_document_graph_prefers_llm_when_available(self) -> None:
        """覆盖 `replace_document_graph_prefers_llm_when_available` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        now = datetime.now(timezone.utc)
        service = GraphService(
            InMemoryState(),
            FakeVectorStoreFactory(),
            TraceRecorder(),
            llm=FakeLLM(
                """
                {
                  "entities": [
                    {"name": "session summary", "entity_type": "concept", "aliases": ["summary"]},
                    {"name": "chat session", "entity_type": "concept", "aliases": ["session"]}
                  ],
                  "relations": [
                    {
                      "source": "session summary",
                      "target": "chat session",
                      "relation": "related_to",
                      "evidence": "session summary 接口关联 chat session。"
                    }
                  ]
                }
                """
            ),
        )
        record = {
            'doc_id': 'doc-1',
            'file_name': 'demo.md',
            'file_path': '/tmp/demo.md',
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': ['guide'],
            'status': 'indexed',
            'chunk_ids': [],
            'indexed_chunks': 0,
            'created_at': now,
            'updated_at': now,
            'indexed_at': now,
            'document_title': '会话摘要说明',
            'document_keywords': ['session summary', 'chat session'],
            'document_summary': '会话摘要用于压缩上下文',
            'document_hierarchy': 'demo',
            'year': '2026',
            'quarter': 'Q2',
            'version': 'v1.0',
            'permission': 'internal',
        }
        segments = [
            {
                'text': 'session summary 接口关联 chat session，并用于压缩历史消息。',
                'section_title': '会话摘要',
                'hierarchy_path': '总览 > 会话摘要',
                'page': 1,
            }
        ]

        result = service.replace_document_graph(record, segments)

        self.assertTrue(result['llm_enabled'])
        self.assertEqual(result['llm_segments'], 1)
        self.assertEqual(result['fallback_segments'], 0)
        node_names = {item['name'] for item in service.state.graph_nodes.values()}
        self.assertEqual(node_names, {'session summary', 'chat session'})
        relations = {
            (item['source_name'], item['relation'], item['target_name'])
            for item in service.state.graph_edges.values()
        }
        self.assertEqual(relations, {('session summary', 'related_to', 'chat session')})

    def test_replace_document_graph_falls_back_when_llm_fails(self) -> None:
        """覆盖 `replace_document_graph_falls_back_when_llm_fails` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        now = datetime.now(timezone.utc)
        trace = TraceRecorder()
        service = GraphService(
            InMemoryState(),
            FakeVectorStoreFactory(),
            trace,
            llm=FakeLLM('', fail=True),
        )
        record = {
            'doc_id': 'doc-1',
            'file_name': 'demo.md',
            'file_path': '/tmp/demo.md',
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': ['guide'],
            'status': 'indexed',
            'chunk_ids': [],
            'indexed_chunks': 0,
            'created_at': now,
            'updated_at': now,
            'indexed_at': now,
            'document_title': '会话摘要说明',
            'document_keywords': ['session summary', 'chat session'],
            'document_summary': '会话摘要用于压缩上下文',
            'document_hierarchy': 'demo',
            'year': '2026',
            'quarter': 'Q2',
            'version': 'v1.0',
            'permission': 'internal',
        }
        segments = [
            {
                'text': 'session summary 接口关联 chat session，并用于压缩历史消息。',
                'section_title': '会话摘要',
                'hierarchy_path': '总览 > 会话摘要',
                'page': 1,
            }
        ]

        result = service.replace_document_graph(record, segments)

        self.assertTrue(result['llm_enabled'])
        self.assertEqual(result['llm_segments'], 0)
        self.assertEqual(result['fallback_segments'], 1)
        self.assertTrue(any(event.name == 'graph_llm_extraction_failed' for event in trace.events))
        node_names = {item['name'] for item in service.state.graph_nodes.values()}
        self.assertIn('session summary', node_names)
        self.assertIn('chat session', node_names)


if __name__ == '__main__':
    unittest.main()

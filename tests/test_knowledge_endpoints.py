"""知识能力端点测试，验证文档上下文、证据检索、落地回答和健康检查响应。"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.capabilities.knowledge import DocumentContextResult, GroundedAnswerResult
from app.capabilities.knowledge.base import DocumentContextItem
from app.capabilities.knowledge.contracts import GroundedAnswerStrategy, RetrievalQualityReport
from app.core.config import Settings
from app.models.artifact import EvidenceItem, EvidencePack


class FakeLocalKnowledgeCapability:
    """测试桩 `FakeLocalKnowledgeCapability`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def load_document_context(self, request):
        return DocumentContextResult(
            documents=[
                DocumentContextItem(
                    doc_id=request.doc_ids[0] if request.doc_ids else 'doc-1',
                    title='Doc',
                    summary='summary',
                    sections=['section'],
                    metadata={'collection_name': request.collection_name},
                )
            ]
        )

    def retrieve_evidence(self, request, *, trace_context=None):
        return EvidencePack(
            task_id='task-1',
            evidence_items=[
                EvidenceItem(
                    citation_id='c1',
                    source='demo.md',
                    chunk_id='chunk-1',
                    text=f'evidence for {request.query}',
                    support_score=0.9,
                )
            ],
            coverage_score=1.0,
            missing_aspects=[],
        )

    def grounded_answer(self, request, *, trace_context=None):
        return GroundedAnswerResult(
            answer=f'answer for {request.question}',
            evidence_pack=EvidencePack(task_id='task-1', evidence_items=[], coverage_score=1.0, missing_aspects=[]),
            citations=[{'citation_id': 'c1'}],
            grounded=True,
            quality_report=RetrievalQualityReport(enabled=True, strategy='crag'),
        )


class FakeContainer:
    """用于端点测试的简化容器桩对象，负责提供最小依赖集合并隔离真实装配过程。"""
    def __init__(self) -> None:
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.local_knowledge_capability = FakeLocalKnowledgeCapability()


class KnowledgeEndpointTests(unittest.TestCase):
    """知识能力端点测试集合，验证上下文、证据与回答接口的契约行为。"""
    def _create_client(self) -> TestClient:
        """构造绑定当前测试配置与依赖的辅助对象，确保断言只依赖本用例显式布置的上下文。"""
        settings_path = Path(tempfile.mkdtemp())
        with patch('app.core.config.get_settings') as mock_get_settings, patch(
            'app.container.build_container', return_value=FakeContainer()
        ):
            from app.core.config import Settings

            mock_get_settings.return_value = Settings(DATA_DIR=settings_path)
            sys.modules.pop('app.main', None)
            main_module = importlib.import_module('app.main')
            app = main_module.create_app()
        return TestClient(app)

    def test_document_context_endpoint(self) -> None:
        """覆盖 `document_context_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/knowledge/document-context',
            json={'request': {'collection_name': 'demo', 'doc_ids': ['doc-1']}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['documents'][0]['doc_id'], 'doc-1')

    def test_health_endpoint(self) -> None:
        """覆盖 `health_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.get('/api/v1/knowledge/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'knowledge_capability')
        self.assertTrue(response.json()['ready'])

    def test_search_endpoint(self) -> None:
        """覆盖 `search_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/knowledge/search',
            json={'request': {'query': 'risk', 'collection_name': 'demo'}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['evidence_items'][0]['citation_id'], 'c1')

    def test_grounded_answer_endpoint(self) -> None:
        """覆盖 `grounded_answer_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/knowledge/grounded-answer',
            json={
                'request': {
                    'question': 'risk?',
                    'collection_name': 'demo',
                    'strategy': GroundedAnswerStrategy().model_dump(mode='json'),
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answer'], 'answer for risk?')


if __name__ == '__main__':
    unittest.main()

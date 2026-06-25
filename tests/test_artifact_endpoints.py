"""制品端点测试，验证制品列表、详情查询和健康状态接口的响应约定。"""

import importlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.capabilities.artifact import build_artifact_capability_from_provider
from app.core.config import Settings
from app.models.artifact import Artifact, ReportArtifactContent
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class FakeContainer:
    """用于端点测试的简化容器桩对象，负责提供最小依赖集合并隔离真实装配过程。"""
    def __init__(self, settings: Settings) -> None:
        self.state = InMemoryState()
        self.persistence = SQLiteStateStore(settings)
        now = datetime.now(timezone.utc)
        artifact = Artifact(
            artifact_id='artifact-1',
            task_id='task-1',
            artifact_type='document_analysis_report',
            version=1,
            status='final',
            content=ReportArtifactContent(
                title='文档分析报告',
                summary='demo summary',
                confidence=0.8,
                report_markdown='# 文档分析报告',
                report_json={'summary': 'demo summary'},
            ),
            created_at=now,
            updated_at=now,
        )
        payload = artifact.model_dump(mode='python')
        self.state.artifacts[artifact.artifact_id] = payload
        self.persistence.upsert_artifact(payload)
        self.local_artifact_capability = build_artifact_capability_from_provider(
            settings=settings,
            state=self.state,
            persistence=self.persistence,
        )


class ArtifactEndpointTests(unittest.TestCase):
    """制品端点测试集合，确保制品查询接口返回结构与状态码一致。"""
    def _create_client(self) -> TestClient:
        """构造绑定当前测试配置与依赖的辅助对象，确保断言只依赖本用例显式布置的上下文。"""
        settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        with patch('app.core.config.get_settings') as mock_get_settings, patch(
            'app.container.build_container', return_value=FakeContainer(settings)
        ):
            mock_get_settings.return_value = settings
            sys.modules.pop('app.main', None)
            main_module = importlib.import_module('app.main')
            app = main_module.create_app()
        return TestClient(app)

    def test_health_endpoint(self) -> None:
        """覆盖 `health_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.get('/api/v1/artifacts/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'artifact_capability')
        self.assertTrue(response.json()['ready'])

    def test_list_artifacts_endpoint(self) -> None:
        """覆盖 `list_artifacts_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post('/api/v1/artifacts/list', json={'task_id': 'task-1', 'limit': 10, 'offset': 0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['artifact_id'], 'artifact-1')

    def test_get_artifact_endpoint(self) -> None:
        """覆盖 `get_artifact_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.get('/api/v1/artifacts/artifact-1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['artifact_type'], 'document_analysis_report')
        self.assertEqual(response.json()['content']['summary'], 'demo summary')


if __name__ == '__main__':
    unittest.main()

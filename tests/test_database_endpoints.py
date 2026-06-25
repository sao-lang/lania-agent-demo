"""数据库端点测试，验证健康探针、表信息查询和只读 SQL 约束。"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.capabilities.database import build_database_capability
from app.core.config import Settings
from app.services.sqlite_store import SQLiteStateStore


class FakeContainer:
    """用于端点测试的简化容器桩对象，负责提供最小依赖集合并隔离真实装配过程。"""
    def __init__(self, settings: Settings) -> None:
        self.local_database_capability = build_database_capability(settings)


class DatabaseEndpointTests(unittest.TestCase):
    """数据库端点测试集合，覆盖健康检查、元数据查询与 SQL 安全约束。"""
    def _create_client(self) -> TestClient:
        """构造绑定当前测试配置与依赖的辅助对象，确保断言只依赖本用例显式布置的上下文。"""
        settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        persistence = SQLiteStateStore(settings)
        persistence.upsert_task({'task_id': 'task-1', 'status': 'running', 'instructions': 'demo'})
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

        response = client.get('/api/v1/database/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'database_capability')
        self.assertTrue(response.json()['ready'])

    def test_list_tables_endpoint(self) -> None:
        """覆盖 `list_tables_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post('/api/v1/database/list-tables', json={'include_system_tables': False, 'max_entries': 20})

        self.assertEqual(response.status_code, 200)
        self.assertIn('tasks', [item['name'] for item in response.json()['tables']])

    def test_describe_table_endpoint(self) -> None:
        """覆盖 `describe_table_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post('/api/v1/database/describe-table', json={'table_name': 'tasks'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('task_id', [item['name'] for item in response.json()['columns']])

    def test_query_endpoint(self) -> None:
        """覆盖 `query_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post('/api/v1/database/query', json={'sql': 'SELECT task_id, payload FROM tasks', 'max_rows': 10})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rows'][0]['task_id'], 'task-1')

    def test_query_endpoint_rejects_write_sql(self) -> None:
        """覆盖 `query_endpoint_rejects_write_sql` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post('/api/v1/database/query', json={'sql': 'DELETE FROM tasks', 'max_rows': 10})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'database_invalid_query')


if __name__ == '__main__':
    unittest.main()

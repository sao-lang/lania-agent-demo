"""API 合同端点测试，验证健康检查、合同列表、接口搜索和合同读取等 HTTP 行为。"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.capabilities.api_contract import build_api_contract_capability


class FakeContainer:
    """用于端点测试的简化容器桩对象，负责提供最小依赖集合并隔离真实装配过程。"""
    def __init__(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / 'openapi.json').write_text(
            '{"openapi":"3.0.0","info":{"title":"Demo API","version":"v1"},"paths":{"/health":{"get":{"operationId":"getHealth","summary":"health endpoint","tags":["system"]}},"/documents":{"post":{"operationId":"createDocument","summary":"create document","tags":["documents"]}}}}',
            encoding='utf-8',
        )
        self.local_api_contract_capability = build_api_contract_capability(root)


class ApiContractEndpointTests(unittest.TestCase):
    """API 合同端点测试集合，确保接口契约相关 HTTP 输出满足预期。"""
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

    def test_health_endpoint(self) -> None:
        """覆盖 `health_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.get('/api/v1/api-contract/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'api_contract_capability')
        self.assertTrue(response.json()['ready'])

    def test_list_contracts_endpoint(self) -> None:
        """覆盖 `list_contracts_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post('/api/v1/api-contract/list-contracts', json={'path_prefix': '.', 'max_entries': 10})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['contracts'][0]['path'], 'openapi.json')
        self.assertEqual(response.json()['contracts'][0]['operation_count'], 2)

    def test_search_operations_endpoint(self) -> None:
        """覆盖 `search_operations_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/api-contract/search-operations',
            json={'query': 'document', 'path_prefix': '.', 'max_results': 10},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['matches'][0]['operation_id'], 'createDocument')

    def test_read_contract_endpoint(self) -> None:
        """覆盖 `read_contract_endpoint` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/api-contract/read-contract',
            json={'path': 'openapi.json', 'method': 'get', 'endpoint_path': '/health'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['title'], 'Demo API')
        self.assertEqual(response.json()['selected_operation']['operation_id'], 'getHealth')


if __name__ == '__main__':
    unittest.main()

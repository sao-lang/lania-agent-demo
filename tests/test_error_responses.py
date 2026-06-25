"""统一错误响应测试，覆盖常见异常场景的错误码、消息结构和 OpenAPI 声明。"""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.collection import CollectionCreateRequest


class ErrorResponseTests(unittest.TestCase):
    """统一错误响应测试集合，确保不同异常来源遵循相同错误封装约定。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_not_found_uses_uniform_error_payload(self) -> None:
        """覆盖 `not_found_uses_uniform_error_payload` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.client.get('/api/v1/sessions/not-exists')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                'error': {
                    'code': 'session_not_found',
                    'message': 'session not found',
                    'details': {
                        'resource': 'session',
                        'identifier': 'not-exists',
                    },
                },
                'path': '/api/v1/sessions/not-exists',
                'timestamp': response.json()['timestamp'],
                'status_code': 404,
            },
        )

    def test_validation_error_uses_uniform_error_payload(self) -> None:
        """覆盖 `validation_error_uses_uniform_error_payload` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.client.post(
            '/api/v1/collections',
            json={
                'name': 'demo',
                'chunk_size': 50,
                'chunk_overlap': 100,
            },
        )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body['error']['code'], 'validation_error')
        self.assertEqual(body['error']['message'], 'request validation failed')
        self.assertEqual(body['path'], '/api/v1/collections')
        self.assertEqual(body['status_code'], 422)
        self.assertTrue(body['error']['details'])

    def test_scan_directory_failure_uses_uniform_error_payload(self) -> None:
        """覆盖 `scan_directory_failure_uses_uniform_error_payload` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        self.app.state.container.collection_service.create(CollectionCreateRequest(name='scan-demo'))
        missing_dir = Path(tempfile.gettempdir()) / f'rag-missing-{uuid4().hex}'

        response = self.client.post(
            '/api/v1/documents/scan',
            json={
                'directory': str(missing_dir),
                'collection_name': 'scan-demo',
                'recursive': True,
                'file_types': [],
            },
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body['error']['code'], 'scan_failed')
        self.assertEqual(body['error']['message'], 'directory not found')
        self.assertEqual(body['error']['details']['collection_name'], 'scan-demo')
        self.assertEqual(body['error']['details']['directory'], str(missing_dir))
        self.assertEqual(body['path'], '/api/v1/documents/scan')
        self.assertEqual(body['status_code'], 400)

    def test_reindex_requires_collection_or_doc_ids(self) -> None:
        """覆盖 `reindex_requires_collection_or_doc_ids` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.client.post(
            '/api/v1/documents/reindex',
            json={},
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body['error']['code'], 'reindex_target_required')
        self.assertEqual(body['error']['message'], 'collection_name 或 doc_ids 至少提供一个')
        self.assertIsNone(body['error']['details'])
        self.assertEqual(body['path'], '/api/v1/documents/reindex')
        self.assertEqual(body['status_code'], 400)

    def test_reindex_missing_collection_uses_not_found_error(self) -> None:
        """覆盖 `reindex_missing_collection_uses_not_found_error` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.client.post(
            '/api/v1/documents/reindex',
            json={'collection_name': 'missing-demo'},
        )

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body['error']['code'], 'collection_not_found')
        self.assertEqual(body['error']['message'], 'collection not found')
        self.assertEqual(body['error']['details']['identifier'], 'missing-demo')
        self.assertEqual(body['path'], '/api/v1/documents/reindex')
        self.assertEqual(body['status_code'], 404)

    def test_openapi_includes_uniform_error_responses(self) -> None:
        """覆盖 `openapi_includes_uniform_error_responses` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.client.get('/openapi.json')

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        responses = schema['paths']['/api/v1/documents/reindex']['post']['responses']
        self.assertIn('400', responses)
        self.assertIn('404', responses)
        self.assertIn('422', responses)
        self.assertIn('500', responses)
        self.assertEqual(
            responses['400']['content']['application/json']['example']['error']['code'],
            'bad_request',
        )

    def test_task_creation_missing_collection_uses_not_found_error(self) -> None:
        """覆盖 `task_creation_missing_collection_uses_not_found_error` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        response = self.client.post(
            '/api/v1/tasks/document-analysis',
            json={
                'collection_name': 'missing-demo',
                'instructions': '总结风险点',
            },
        )

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body['error']['code'], 'collection_not_found')
        self.assertEqual(body['error']['details']['identifier'], 'missing-demo')
        self.assertEqual(body['path'], '/api/v1/tasks/document-analysis')


if __name__ == '__main__':
    unittest.main()

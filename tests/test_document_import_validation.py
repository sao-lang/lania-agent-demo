"""文档导入校验测试，关注上传阶段的大小限制和内容类型校验错误输出。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.collection import CollectionCreateRequest


class DocumentImportValidationTests(unittest.TestCase):
    """文档导入校验测试集合，确保上传校验错误以结构化形式返回。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.settings = Settings(
            DATA_DIR=Path(tempfile.mkdtemp()),
            MAX_IMPORT_FILE_BYTES=4,
        )

    def _create_client(self) -> TestClient:
        """构造绑定当前测试配置与依赖的辅助对象，确保断言只依赖本用例显式布置的上下文。"""
        with patch('app.main.get_settings', return_value=self.settings):
            app = create_app()
        app.state.container.collection_service.create(CollectionCreateRequest(name='demo'))
        return TestClient(app)

    def test_upload_returns_structured_failure_for_oversized_file(self) -> None:
        """覆盖 `upload_returns_structured_failure_for_oversized_file` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/documents/upload',
            data={'collection_name': 'demo'},
            files={'files': ('large.txt', b'12345', 'text/plain')},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['uploaded'], [])
        self.assertEqual(body['stats']['input_files'], 1)
        self.assertEqual(body['stats']['failed_files'], 1)
        self.assertEqual(body['stats']['imported_documents'], 0)
        self.assertEqual(body['failed'][0]['code'], 'file_too_large')
        self.assertEqual(body['failed'][0]['stage'], 'validation')
        self.assertEqual(body['failed'][0]['file_type'], 'txt')

    def test_upload_returns_structured_failure_for_content_type_mismatch(self) -> None:
        """覆盖 `upload_returns_structured_failure_for_content_type_mismatch` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/documents/upload',
            data={'collection_name': 'demo'},
            files={'files': ('demo.pdf', b'1234', 'text/plain')},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['uploaded'], [])
        self.assertEqual(body['stats']['failed_files'], 1)
        self.assertEqual(body['failed'][0]['code'], 'content_type_mismatch')
        self.assertEqual(body['failed'][0]['stage'], 'validation')
        self.assertEqual(body['failed'][0]['file_type'], 'pdf')


if __name__ == '__main__':
    unittest.main()

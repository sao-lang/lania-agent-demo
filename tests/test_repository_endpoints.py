"""验证仓库能力相关接口的 HTTP 行为。"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.capabilities.repository import build_repository_capability


class FakeContainer:
    """提供带有本地仓库能力的最小容器桩对象。"""

    def __init__(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / 'README.md').write_text('# Demo\nrepository capability works\n', encoding='utf-8')
        (root / 'src').mkdir()
        (root / 'src' / 'module.py').write_text('print("repository capability")\n', encoding='utf-8')
        self.local_repository_capability = build_repository_capability(root)


class RepositoryEndpointTests(unittest.TestCase):
    """覆盖仓库健康检查、列目录、搜索与读文件接口。"""

    def _create_client(self) -> TestClient:
        """在隔离配置下创建挂载假容器的测试客户端。"""
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
        """验证健康检查接口会暴露仓库能力的可用状态。"""
        client = self._create_client()

        response = client.get('/api/v1/repository/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'repository_capability')
        self.assertTrue(response.json()['ready'])

    def test_list_files_endpoint(self) -> None:
        """验证列文件接口会返回仓库中的目录与文件条目。"""
        client = self._create_client()

        response = client.post('/api/v1/repository/list-files', json={'path_prefix': '.', 'recursive': True, 'max_entries': 10})

        self.assertEqual(response.status_code, 200)
        paths = [item['path'] for item in response.json()['entries']]
        self.assertIn('README.md', paths)
        self.assertIn('src/module.py', paths)

    def test_search_endpoint(self) -> None:
        """验证搜索接口会返回匹配内容所在的文件路径。"""
        client = self._create_client()

        response = client.post('/api/v1/repository/search', json={'query': 'repository capability', 'path_prefix': '.', 'max_results': 5})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['matches'][0]['path'], 'README.md')

    def test_read_file_endpoint(self) -> None:
        """验证读文件接口会返回指定范围内的文件内容。"""
        client = self._create_client()

        response = client.post('/api/v1/repository/read-file', json={'path': 'src/module.py', 'start_line': 1, 'max_lines': 20})

        self.assertEqual(response.status_code, 200)
        self.assertIn('repository capability', response.json()['content'])


if __name__ == '__main__':
    unittest.main()

"""验证本地沙箱工具接口的执行与发现能力。"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.agents.tools.artifact_tools import DraftReportOutput
from app.core.config import Settings
from app.harness.sandbox import build_default_sandbox_worker_registry
from app.models.artifact import ReviewResult


class FakeLocalSandboxEngine:
    """模拟具备本地隔离执行能力的沙箱引擎。"""

    def __init__(self) -> None:
        self.worker_registry = build_default_sandbox_worker_registry()

    def execute_local_isolated(self, *, tool_name, payload, timeout_ms, output_model: type[BaseModel]):
        if output_model is ReviewResult:
            return output_model.model_validate(
                {
                    'passed': True,
                    'unsupported_claims': [],
                    'missing_sections': [],
                    'review_notes': [f'local sandbox for {tool_name}'],
                }
            )
        if output_model is DraftReportOutput:
            return output_model.model_validate(
                {
                    'content': {
                        'summary': f'local sandbox for {tool_name}',
                        'key_findings': [],
                        'risks': [],
                        'evidence': [],
                        'open_questions': [],
                        'confidence': 0.9,
                        'report_markdown': '# 文档分析报告',
                        'report_json': {'summary': f'local sandbox for {tool_name}'},
                    }
                }
            )
        return output_model.model_validate(
            {
                'summary': f'local sandbox for {tool_name}',
                'key_findings': [],
                'risks': [],
                'evidence': [],
                'open_questions': [],
                'confidence': 0.9,
                'report_markdown': '# 文档分析报告',
                'report_json': {'summary': f'local sandbox for {tool_name}'},
            }
        )

    def list_worker_tools(self):
        return self.worker_registry.catalog()

    def describe_worker_tool(self, tool_name: str):
        return self.worker_registry.catalog().tools[[tool.tool_name for tool in self.worker_registry.catalog().tools].index(tool_name)]


class FakeContainer:
    """向应用注入假沙箱引擎的最小容器对象。"""

    def __init__(self) -> None:
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.local_sandbox_engine = FakeLocalSandboxEngine()


class SandboxEndpointTests(unittest.TestCase):
    """覆盖沙箱执行、工具发现和健康检查接口。"""

    def _create_client(self) -> TestClient:
        """创建挂接假沙箱容器的测试客户端。"""
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

    def test_execute_tool_endpoint(self) -> None:
        """验证执行普通工具接口会返回沙箱模式和工具输出。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/sandbox/execute-tool',
            json={
                'tool_name': 'finalize_report',
                'payload': {
                    'content': {
                        'summary': 'draft',
                        'key_findings': [],
                        'risks': [],
                        'evidence': [],
                        'open_questions': [],
                        'confidence': 0.9,
                    },
                    'output_format': 'markdown+json',
                },
                'timeout_ms': 3000,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['tool_name'], 'finalize_report')
        self.assertEqual(body['sandbox_mode'], 'process_isolated')
        self.assertEqual(body['data']['summary'], 'local sandbox for finalize_report')

    def test_execute_review_tool_endpoint(self) -> None:
        """验证执行审查类工具接口会返回结构化审查结果。"""
        client = self._create_client()

        response = client.post(
            '/api/v1/sandbox/execute-tool',
            json={
                'tool_name': 'review_report',
                'payload': {
                    'content': {
                        'summary': 'draft',
                        'key_findings': [],
                        'risks': [],
                        'evidence': [],
                        'open_questions': [],
                        'confidence': 0.9,
                        'report_markdown': '# 文档分析报告',
                        'report_json': {'summary': 'draft'},
                    }
                },
                'timeout_ms': 3000,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['tool_name'], 'review_report')
        self.assertTrue(body['data']['passed'])
        self.assertEqual(body['data']['review_notes'], ['local sandbox for review_report'])

    def test_list_tools_endpoint(self) -> None:
        """验证工具列表接口会暴露当前支持的沙箱工具。"""
        client = self._create_client()

        response = client.get('/api/v1/sandbox/tools')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item['tool_name'] for item in body['tools']], ['draft_report', 'finalize_report', 'review_report'])

    def test_get_tool_schema_endpoint(self) -> None:
        """验证单个工具 schema 接口会返回风险等级等元信息。"""
        client = self._create_client()

        response = client.get('/api/v1/sandbox/tools/review_report')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['tool_name'], 'review_report')
        self.assertEqual(body['risk_level'], 'medium')

    def test_health_endpoint(self) -> None:
        """验证健康检查接口会返回支持工具数量与名称列表。"""
        client = self._create_client()

        response = client.get('/api/v1/sandbox/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'sandbox_worker')
        self.assertTrue(response.json()['ready'])
        self.assertEqual(response.json()['supported_tools_count'], 3)
        self.assertEqual(response.json()['supported_tools'], ['draft_report', 'finalize_report', 'review_report'])


if __name__ == '__main__':
    unittest.main()

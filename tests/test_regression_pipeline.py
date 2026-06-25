"""验证回归流水线报告生成脚本的输出结构。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_regression_pipeline import build_pipeline_markdown_report, generate_regression_pipeline


class RegressionPipelineTests(unittest.TestCase):
    """覆盖回归流水线 Markdown 汇总与产物落盘行为。"""

    def test_build_pipeline_markdown_report_renders_sections(self) -> None:
        """验证汇总报告会输出核心章节和门禁结论。"""
        payload = {
            'generated_at': '2026-06-03T12:00:00',
            'dataset_path': '/tmp/eval.json',
            'collection_name': 'demo',
            'pipeline_status': 'warn',
            'gate': {
                'status': 'warn',
                'candidate_strategy': 'accuracy_full_stack',
                'recommendation': '建议先灰度',
                'reasons': ['延迟偏高'],
            },
            'accuracy_report': {
                'json_path': '/tmp/accuracy-report.json',
                'markdown_path': '/tmp/accuracy-report.md',
            },
            'trend_report': {
                'json_path': '/tmp/accuracy-trend.json',
                'markdown_path': '/tmp/accuracy-trend.md',
                'trend_payload': {
                    'insights': ['最近一次回归门禁为 `warn`。'],
                },
            },
        }

        markdown = build_pipeline_markdown_report(payload)

        self.assertIn('# Regression Pipeline Report', markdown)
        self.assertIn('## Pipeline Summary', markdown)
        self.assertIn('## Gate', markdown)
        self.assertIn('## Trend Highlights', markdown)
        self.assertIn('流水线状态: `warn`', markdown)
        self.assertIn('候选策略: `accuracy_full_stack`', markdown)

    @patch('scripts.run_regression_pipeline.generate_trend_report')
    @patch('scripts.run_regression_pipeline.generate_accuracy_report')
    def test_generate_regression_pipeline_writes_summary_files(
        self,
        mock_generate_accuracy_report,
        mock_generate_trend_report,
    ) -> None:
        """验证总控脚本会写出 JSON/Markdown 汇总文件并串联子报告。"""
        mock_generate_accuracy_report.return_value = {
            'gate': {
                'status': 'pass',
                'candidate_strategy': 'accuracy_full_stack',
                'recommendation': '建议切换',
                'reasons': ['指标更优'],
            },
            'json_path': '/tmp/accuracy-report.json',
            'markdown_path': '/tmp/accuracy-report.md',
        }
        mock_generate_trend_report.return_value = {
            'report_count': 4,
            'json_path': '/tmp/accuracy-trend.json',
            'markdown_path': '/tmp/accuracy-trend.md',
            'trend_payload': {
                'insights': ['最近一次回归门禁为 `pass`。'],
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = generate_regression_pipeline(
                dataset_path='/tmp/eval.json',
                collection_name='demo',
                output_dir=temp_dir,
                report_name='pipeline-summary',
                accuracy_report_name='accuracy-summary',
                trend_report_name='trend-summary',
                trend_limit=5,
            )
            json_path = Path(output['json_path'])
            markdown_path = Path(output['markdown_path'])

            self.assertEqual(output['pipeline_status'], 'pass')
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            self.assertIn('pipeline-summary', json_path.name)
            self.assertIn('# Regression Pipeline Report', markdown_path.read_text(encoding='utf-8'))
            mock_generate_accuracy_report.assert_called_once()
            mock_generate_trend_report.assert_called_once()


if __name__ == '__main__':
    unittest.main()

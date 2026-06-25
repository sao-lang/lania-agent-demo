"""文档分析回归流水线测试，验证汇总报告生成以及基准与趋势报告的串联。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_document_analysis_regression_pipeline import (
    build_pipeline_markdown_report,
    generate_document_analysis_regression_pipeline,
)


class DocumentAnalysisPipelineTests(unittest.TestCase):
    """文档分析回归流水线测试集合，验证流水线汇总输出及其依赖报告的衔接。"""
    def test_build_pipeline_markdown_report_renders_sections(self) -> None:
        """覆盖 `build_pipeline_markdown_report_renders_sections` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'generated_at': '2026-06-03T12:00:00',
            'dataset_path': '/tmp/eval.json',
            'collection_name': 'demo',
            'pipeline_status': 'warn',
            'gate': {
                'status': 'warn',
                'recommendation': '建议灰度',
                'reasons': ['review_replan_rate 偏高'],
            },
            'benchmark_report': {
                'json_path': '/tmp/document-analysis-benchmark.json',
                'markdown_path': '/tmp/document-analysis-benchmark.md',
            },
            'trend_report': {
                'json_path': '/tmp/document-analysis-trend.json',
                'markdown_path': '/tmp/document-analysis-trend.md',
                'trend_payload': {'insights': ['最近一次任务 benchmark 门禁为 `warn`。']},
            },
        }
        markdown = build_pipeline_markdown_report(payload)
        self.assertIn('# Document Analysis Regression Pipeline', markdown)
        self.assertIn('## Pipeline Summary', markdown)
        self.assertIn('## Gate', markdown)
        self.assertIn('## Trend Highlights', markdown)
        self.assertIn('流水线状态: `warn`', markdown)

    @patch('scripts.run_document_analysis_regression_pipeline.generate_document_analysis_trend_report')
    @patch('scripts.run_document_analysis_regression_pipeline.generate_document_analysis_benchmark_report')
    def test_generate_pipeline_writes_summary_files(
        self,
        mock_generate_benchmark_report,
        mock_generate_trend_report,
    ) -> None:
        """覆盖 `generate_pipeline_writes_summary_files` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        mock_generate_benchmark_report.return_value = {
            'gate': {'status': 'pass', 'recommendation': '建议通过', 'reasons': ['指标满足要求']},
            'json_path': '/tmp/document-analysis-benchmark.json',
            'markdown_path': '/tmp/document-analysis-benchmark.md',
        }
        mock_generate_trend_report.return_value = {
            'report_count': 4,
            'json_path': '/tmp/document-analysis-trend.json',
            'markdown_path': '/tmp/document-analysis-trend.md',
            'trend_payload': {'insights': ['最近一次任务 benchmark 门禁为 `pass`。']},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = generate_document_analysis_regression_pipeline(
                dataset_path='/tmp/eval.json',
                collection_name='demo',
                output_dir=temp_dir,
                report_name='document-analysis-pipeline-summary',
                benchmark_report_name='document-analysis-benchmark-summary',
                trend_report_name='document-analysis-trend-summary',
                trend_limit=5,
            )
            json_path = Path(output['json_path'])
            markdown_path = Path(output['markdown_path'])
            self.assertEqual(output['pipeline_status'], 'pass')
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            self.assertIn('# Document Analysis Regression Pipeline', markdown_path.read_text(encoding='utf-8'))
            mock_generate_benchmark_report.assert_called_once()
            mock_generate_trend_report.assert_called_once()
            self.assertEqual(mock_generate_trend_report.call_args.kwargs['collection_name'], 'demo')


if __name__ == '__main__':
    unittest.main()

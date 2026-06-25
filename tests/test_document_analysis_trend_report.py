"""文档分析趋势报告测试，覆盖报告加载、集合过滤和 Markdown 趋势摘要渲染。"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_document_analysis_trend_report import (
    build_markdown_report,
    build_trend_payload,
    load_benchmark_reports,
)


class DocumentAnalysisTrendReportTests(unittest.TestCase):
    """文档分析趋势报告测试集合，确保趋势统计和筛选逻辑保持一致。"""
    def test_load_reports_and_build_trend_payload(self) -> None:
        """覆盖 `load_reports_and_build_trend_payload` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._write_report(
                base / 'document-analysis-benchmark-001.json',
                benchmark_id='bench-1',
                completed_at='2026-06-03T10:00:00',
                gate_status='pass',
                avg_score=0.82,
                avg_coverage=0.71,
                avg_cost=3.2,
            )
            self._write_report(
                base / 'document-analysis-benchmark-002.json',
                benchmark_id='bench-2',
                completed_at='2026-06-03T11:00:00',
                gate_status='warn',
                avg_score=0.88,
                avg_coverage=0.79,
                avg_cost=3.8,
            )
            reports = load_benchmark_reports(base, limit=10)
            trend = build_trend_payload(reports)

        self.assertEqual(len(reports), 2)
        self.assertEqual(trend['report_count'], 2)
        self.assertEqual(trend['latest_benchmark_id'], 'bench-2')
        self.assertEqual(trend['gate_counts'], {'pass': 1, 'warn': 1})
        self.assertEqual(trend['metric_trends'][1]['metric'], 'avg_score')
        self.assertAlmostEqual(trend['metric_trends'][1]['delta'], 0.06, places=4)
        self.assertIn('tech_design', trend['latest_bucket_breakdown'])
        self.assertEqual(trend['latest_worst_samples'][0]['bucket'], 'tech_design')
        self.assertIn('最近一次任务 benchmark 门禁为 `warn`', trend['insights'][0])

    def test_load_reports_supports_collection_filter(self) -> None:
        """覆盖 `load_reports_supports_collection_filter` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._write_report(
                base / 'document-analysis-benchmark-001.json',
                benchmark_id='bench-1',
                completed_at='2026-06-03T10:00:00',
                gate_status='pass',
                avg_score=0.82,
                avg_coverage=0.71,
                avg_cost=3.2,
                collection_name='demo',
            )
            self._write_report(
                base / 'document-analysis-benchmark-002.json',
                benchmark_id='bench-2',
                completed_at='2026-06-03T11:00:00',
                gate_status='warn',
                avg_score=0.88,
                avg_coverage=0.79,
                avg_cost=3.8,
                collection_name='demo-2',
            )
            reports = load_benchmark_reports(base, limit=10, collection_name='demo')

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['result']['collection_name'], 'demo')

    def test_build_markdown_report_renders_sections(self) -> None:
        """覆盖 `build_markdown_report_renders_sections` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'report_count': 2,
            'latest_report_path': '/tmp/document-analysis-benchmark-002.json',
            'latest_completed_at': '2026-06-03T11:00:00',
            'latest_benchmark_id': 'bench-2',
            'latest_collection_name': 'demo',
            'insights': ['最近一次任务 benchmark 门禁为 `pass`。'],
            'gate_counts': {'pass': 1, 'warn': 1},
            'gate_history': [
                {
                    'completed_at': '2026-06-03T11:00:00',
                    'benchmark_id': 'bench-2',
                    'gate_status': 'warn',
                    'recommendation': '建议灰度',
                }
            ],
            'metric_trends': [
                {'metric': 'avg_score', 'first_value': 0.82, 'latest_value': 0.88, 'delta': 0.06}
            ],
            'tool_trends': [
                {'tool_name': 'review_report', 'latest_error_rate': 0.2, 'latest_avg_duration_ms': 12.0}
            ],
            'latest_bucket_breakdown': {
                'tech_design': {
                    'sample_count': 2,
                    'success_rate': 1.0,
                    'avg_score': 0.88,
                    'avg_evidence_coverage': 0.79,
                    'avg_evidence_usability_score': 0.91,
                }
            },
            'latest_worst_samples': [
                {
                    'index': 1,
                    'bucket': 'tech_design',
                    'status': 'completed',
                    'score': 0.88,
                    'evidence_coverage': 0.79,
                    'evidence_usability_score': 0.91,
                }
            ],
        }
        markdown = build_markdown_report(payload)
        self.assertIn('# Document Analysis Trend Report', markdown)
        self.assertIn('## Trend Insights', markdown)
        self.assertIn('## Gate Summary', markdown)
        self.assertIn('## Gate History', markdown)
        self.assertIn('## Metric Trends', markdown)
        self.assertIn('## Tool Trends', markdown)
        self.assertIn('## Latest Bucket Breakdown', markdown)
        self.assertIn('## Latest Worst Samples', markdown)
        self.assertIn('| avg_score | 0.8200 | 0.8800 | +0.0600 |', markdown)
        self.assertIn('| review_report | 0.2000 | 12.0000 |', markdown)
        self.assertIn('| tech_design | 2 | 1.0000 | 0.8800 | 0.7900 | 0.9100 |', markdown)

    @staticmethod
    def _write_report(
        path: Path,
        *,
        benchmark_id: str,
        completed_at: str,
        gate_status: str,
        avg_score: float,
        avg_coverage: float,
        avg_cost: float,
        collection_name: str = 'demo',
    ) -> None:
        payload = {
            'report_mode': 'document_analysis_benchmark',
            'gate': {
                'status': gate_status,
                'recommendation': 'test',
            },
            'dashboard_summary': {
                'benchmark_id': benchmark_id,
                'collection_name': collection_name,
                'sample_count': 1,
                'success_count': 1,
                'failed_count': 0,
                'success_rate': 1.0,
                'avg_score': avg_score,
                'avg_evidence_coverage': avg_coverage,
                'avg_estimated_cost_units': avg_cost,
                'bucket_breakdown': {
                    'tech_design': {
                        'sample_count': 1,
                        'success_rate': 1.0,
                        'avg_score': avg_score,
                        'avg_evidence_coverage': avg_coverage,
                        'avg_evidence_usability_score': 0.9,
                    }
                },
                'worst_samples': [
                    {
                        'index': 1,
                        'bucket': 'tech_design',
                        'status': 'completed',
                        'score': avg_score,
                        'evidence_coverage': avg_coverage,
                        'evidence_usability_score': 0.9,
                    }
                ],
                'tool_breakdown': {
                    'review_report': {'error_rate': 0.2, 'avg_duration_ms': 12.0}
                },
            },
            'result': {
                'benchmark_id': benchmark_id,
                'collection_name': collection_name,
                'completed_at': completed_at,
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()

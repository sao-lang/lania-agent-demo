"""准确率趋势报告相关测试，覆盖历史报告加载、趋势聚合和 Markdown 报表渲染。"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_accuracy_trend_report import build_markdown_report, build_trend_payload, load_accuracy_reports


class AccuracyTrendReportTests(unittest.TestCase):
    """准确率趋势报告测试集合，验证历史报告聚合后的趋势摘要是否稳定。"""
    def test_load_accuracy_reports_and_build_trend_payload(self) -> None:
        """覆盖 `load_accuracy_reports_and_build_trend_payload` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._write_report(
                base / 'accuracy-report-001.json',
                completed_at='2026-06-03T10:00:00',
                gate_status='pass',
                candidate='rewrite_hybrid_rerank',
                faithfulness_best=0.82,
                faithfulness_strategy='rewrite_hybrid_rerank',
                bucket_best='rewrite_hybrid_rerank',
            )
            self._write_report(
                base / 'accuracy-report-002.json',
                completed_at='2026-06-03T11:00:00',
                gate_status='warn',
                candidate='accuracy_full_stack',
                faithfulness_best=0.91,
                faithfulness_strategy='accuracy_full_stack',
                bucket_best='accuracy_full_stack',
            )
            self._write_report(
                base / 'accuracy-report-003.json',
                completed_at='2026-06-03T12:00:00',
                gate_status='fail',
                candidate='accuracy_full_stack',
                faithfulness_best=0.88,
                faithfulness_strategy='accuracy_full_stack',
                bucket_best='rewrite_hybrid_rerank',
            )

            reports = load_accuracy_reports(base, prefix='accuracy-report-', limit=10)
            trend = build_trend_payload(reports)

        self.assertEqual(len(reports), 3)
        self.assertEqual(trend['report_count'], 3)
        self.assertEqual(trend['latest_candidate_strategy'], 'accuracy_full_stack')
        self.assertEqual(trend['gate_counts'], {'fail': 1, 'pass': 1, 'warn': 1})
        self.assertEqual(trend['metric_trends'][0]['metric'], 'faithfulness')
        self.assertAlmostEqual(trend['metric_trends'][0]['delta'], 0.06, places=4)
        self.assertEqual(trend['volatile_buckets'][0]['bucket'], 'policy')
        self.assertEqual(trend['volatile_buckets'][0]['change_count'], 2)
        self.assertIn('最近一次回归门禁为 `fail`', trend['insights'][0])

    def test_build_markdown_report_renders_trend_sections(self) -> None:
        """覆盖 `build_markdown_report_renders_trend_sections` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'report_count': 3,
            'latest_report_path': '/tmp/accuracy-report-003.json',
            'latest_completed_at': '2026-06-03T12:00:00',
            'latest_baseline_name': 'rewrite_hybrid_rerank',
            'latest_candidate_strategy': 'accuracy_full_stack',
            'insights': ['最近一次回归门禁为 `warn`，建议先灰度。'],
            'gate_counts': {'pass': 1, 'warn': 2},
            'gate_history': [
                {
                    'completed_at': '2026-06-03T11:00:00',
                    'report_mode': 'replay_compare',
                    'baseline_name': 'rewrite_hybrid_rerank',
                    'candidate_strategy': 'accuracy_full_stack',
                    'gate_status': 'warn',
                    'recommendation': '建议先灰度',
                }
            ],
            'metric_trends': [
                {
                    'metric': 'faithfulness',
                    'first_best_value': 0.82,
                    'latest_best_value': 0.91,
                    'delta': 0.09,
                    'latest_best_strategy': 'accuracy_full_stack',
                    'baseline_latest': 0.84,
                }
            ],
            'volatile_buckets': [
                {
                    'bucket': 'policy',
                    'metric': 'faithfulness',
                    'change_count': 2,
                    'latest_best_strategy': 'accuracy_full_stack',
                }
            ],
        }

        markdown = build_markdown_report(payload)

        self.assertIn('# Accuracy Trend Report', markdown)
        self.assertIn('## Trend Insights', markdown)
        self.assertIn('## Gate Summary', markdown)
        self.assertIn('## Gate History', markdown)
        self.assertIn('## Metric Trends', markdown)
        self.assertIn('## Bucket Volatility', markdown)
        self.assertIn('| Gate Status | Count |', markdown)
        self.assertIn('| faithfulness | 0.8200 | 0.9100 | +0.0900 | accuracy_full_stack | 0.8400 |', markdown)
        self.assertIn('| policy | faithfulness | 2 | accuracy_full_stack |', markdown)

    @staticmethod
    def _write_report(
        path: Path,
        *,
        completed_at: str,
        gate_status: str,
        candidate: str,
        faithfulness_best: float,
        faithfulness_strategy: str,
        bucket_best: str,
    ) -> None:
        payload = {
            'report_mode': 'ragas_compare',
            'gate': {
                'status': gate_status,
                'candidate_strategy': candidate,
                'recommendation': 'test',
            },
            'result': {
                'compare_id': path.stem,
                'dataset_path': '/tmp/eval.json',
                'collection_name': 'demo',
                'baseline_name': 'rewrite_hybrid_rerank',
                'completed_at': completed_at,
                'metrics': {
                    'faithfulness': {
                        'baseline': 0.8,
                        'best_strategy': faithfulness_strategy,
                        'best_value': faithfulness_best,
                    }
                },
                'bucket_metrics': {
                    'policy': {
                        'faithfulness': {
                            'baseline': 0.8,
                            'best_strategy': bucket_best,
                            'best_value': faithfulness_best,
                        }
                    }
                },
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()

"""准确率评估报告相关测试，覆盖对比摘要、回放报告和发布门禁等关键输出的渲染与判定逻辑。"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_accuracy_report import _build_release_gate, build_markdown_report


class AccuracyReportTests(unittest.TestCase):
    """准确率报告测试集合，聚焦报告内容完整性、数据覆盖度和发布门禁判定。"""
    def test_build_markdown_report_renders_compare_summary(self) -> None:
        """覆盖 `build_markdown_report_renders_compare_summary` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'compare_id': 'cmp-1234',
            'dataset_path': '/tmp/eval.json',
            'collection_name': 'demo',
            'baseline_name': 'rewrite_hybrid_rerank',
            'completed_at': '2026-06-03T11:00:00Z',
            'summary': 'done',
            'strategies': [
                {
                    'strategy': {
                        'name': 'rewrite_hybrid_rerank',
                        'use_query_rewrite': True,
                        'use_hybrid_retrieval': True,
                        'use_rerank': True,
                        'use_parent_chunk_retrieval': False,
                        'use_question_oriented_index': False,
                        'use_corrective_rag': False,
                    },
                    'task': {'status': 'completed', 'sample_count': 20, 'success_count': 20, 'metrics': {'faithfulness': 0.82}},
                },
                {
                    'strategy': {
                        'name': 'accuracy_full_stack',
                        'use_query_rewrite': True,
                        'use_hybrid_retrieval': True,
                        'use_rerank': True,
                        'use_parent_chunk_retrieval': True,
                        'use_question_oriented_index': True,
                        'use_corrective_rag': True,
                        'use_graph_rag': True,
                        'graph_max_hops': 2,
                    },
                    'task': {
                        'status': 'completed',
                        'sample_count': 20,
                        'success_count': 20,
                        'metrics': {
                            'faithfulness': 0.91,
                            'answer_relevancy': 0.88,
                            'context_precision': 0.84,
                            'context_recall': 0.86,
                        },
                    },
                },
            ],
            'metrics': {
                'faithfulness': {
                    'baseline': 0.82,
                    'best_strategy': 'accuracy_full_stack',
                    'best_value': 0.91,
                    'deltas': {'rewrite_hybrid_rerank': 0.0, 'accuracy_full_stack': 0.09},
                }
            },
            'bucket_metrics': {
                'faq': {
                    'faithfulness': {
                        'baseline': 0.8,
                        'best_strategy': 'accuracy_full_stack',
                        'best_value': 0.92,
                        'deltas': {'rewrite_hybrid_rerank': 0.0, 'accuracy_full_stack': 0.12},
                    }
                },
                'policy': {
                    'context_recall': {
                        'baseline': 0.75,
                        'best_strategy': 'accuracy_full_stack',
                        'best_value': 0.9,
                        'deltas': {'rewrite_hybrid_rerank': 0.0, 'accuracy_full_stack': 0.15},
                    }
                }
            },
        }

        markdown = build_markdown_report(payload)

        self.assertIn('# Accuracy Regression Report: cmp-1234', markdown)
        self.assertIn('## Report Insights', markdown)
        self.assertIn(
            '建议优先采用这组开关组合：`query_rewrite` + `hybrid_retrieval` + `rerank` + `parent_chunk_retrieval` + `question_oriented_index` + `corrective_rag` + `graph_rag(2hop)`。',
            markdown,
        )
        self.assertIn('## Release Gate', markdown)
        self.assertIn('候选策略: `accuracy_full_stack`', markdown)
        self.assertIn('默认建议优先考虑 `accuracy_full_stack`', markdown)
        self.assertIn('当前最明显的增益点是 `faithfulness`', markdown)
        self.assertIn('建议重点查看这些 bucket 的策略差异：`faq`、`policy`。', markdown)
        self.assertIn('## Strategy Flags', markdown)
        self.assertIn('## Strategy Summary', markdown)
        self.assertIn('## Metric Summary', markdown)
        self.assertIn('## Bucket Metrics', markdown)
        self.assertIn('| Strategy | Rewrite | Hybrid | Rerank | Parent | Q-Index | Corrective | Graph | Hops |', markdown)
        self.assertIn('| accuracy_full_stack | Y | Y | Y | Y | Y | Y | Y | 2 |', markdown)
        self.assertIn('| faithfulness | 0.8200 | accuracy_full_stack | 0.9100 | rewrite_hybrid_rerank=+0.0000; accuracy_full_stack=+0.0900 |', markdown)
        self.assertIn('| faq | faithfulness | 0.8000 | accuracy_full_stack | 0.9200 |', markdown)

    def test_accuracy_regression_dataset_covers_multiple_buckets(self) -> None:
        """覆盖 `accuracy_regression_dataset_covers_multiple_buckets` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        dataset_path = Path(__file__).resolve().parents[1] / 'data' / 'eval' / 'accuracy_regression_eval.json'
        rows = json.loads(dataset_path.read_text(encoding='utf-8'))

        self.assertGreaterEqual(len(rows), 20)
        buckets = {item['bucket'] for item in rows}
        self.assertTrue({'faq', 'policy', 'terminology', 'long_summary'}.issubset(buckets))

    def test_build_markdown_report_supports_replay_compare_shape(self) -> None:
        """覆盖 `build_markdown_report_supports_replay_compare_shape` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'compare_id': 'replay-1',
            'dataset_path': '/tmp/eval.json',
            'collection_name': 'demo',
            'baseline_name': 'rewrite_hybrid_rerank',
            'completed_at': '2026-06-03T11:00:00Z',
            'summary': 'done',
            'strategies': [
                {
                    'strategy': {
                        'name': 'rewrite_hybrid_rerank',
                        'use_query_rewrite': True,
                        'use_hybrid_retrieval': True,
                        'use_rerank': True,
                    },
                    'sample_count': 20,
                    'success_count': 18,
                    'avg_retrieved_count': 4.2,
                    'avg_latency_ms': 210.5,
                }
            ],
            'metrics': {
                'success_rate': {
                    'baseline': 0.9,
                    'best_strategy': 'rewrite_hybrid_rerank',
                    'best_value': 0.9,
                    'deltas': {'rewrite_hybrid_rerank': 0.0},
                }
            },
        }

        markdown = build_markdown_report(payload)

        self.assertIn('## Report Insights', markdown)
        self.assertIn('当前默认基线仍建议保持 `rewrite_hybrid_rerank`', markdown)
        self.assertIn('## Release Gate', markdown)
        self.assertIn('| Strategy | Samples | Success | Success Rate | Avg Retrieved | Avg Latency |', markdown)
        self.assertIn('| rewrite_hybrid_rerank | 20 | 18 | 0.9000 | 4.2000 | 210.5000 |', markdown)

    def test_release_gate_warns_on_latency_tradeoff(self) -> None:
        """覆盖 `release_gate_warns_on_latency_tradeoff` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'baseline_name': 'rewrite_hybrid_rerank',
            'strategies': [
                {
                    'strategy': {'name': 'rewrite_hybrid_rerank'},
                    'sample_count': 20,
                    'success_count': 18,
                    'avg_latency_ms': 180.0,
                },
                {
                    'strategy': {'name': 'accuracy_full_stack'},
                    'sample_count': 20,
                    'success_count': 19,
                    'avg_latency_ms': 310.0,
                },
            ],
            'metrics': {
                'success_rate': {
                    'baseline': 0.9,
                    'best_strategy': 'accuracy_full_stack',
                    'best_value': 0.95,
                    'deltas': {'rewrite_hybrid_rerank': 0.0, 'accuracy_full_stack': 0.05},
                }
            },
        }

        gate = _build_release_gate(payload, max_latency_increase_ms=80.0, max_quality_regression=0.0)

        self.assertEqual(gate['status'], 'warn')
        self.assertEqual(gate['candidate_strategy'], 'accuracy_full_stack')
        self.assertIn('平均延迟比基线高', ' '.join(gate['reasons']))

    def test_release_gate_fails_on_quality_regression(self) -> None:
        """覆盖 `release_gate_fails_on_quality_regression` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        payload = {
            'baseline_name': 'rewrite_hybrid_rerank',
            'strategies': [
                {'strategy': {'name': 'rewrite_hybrid_rerank'}},
                {'strategy': {'name': 'accuracy_full_stack'}},
            ],
            'metrics': {
                'faithfulness': {
                    'baseline': 0.82,
                    'best_strategy': 'accuracy_full_stack',
                    'best_value': 0.91,
                    'deltas': {'rewrite_hybrid_rerank': 0.0, 'accuracy_full_stack': 0.09},
                },
                'context_precision': {
                    'baseline': 0.85,
                    'best_strategy': 'rewrite_hybrid_rerank',
                    'best_value': 0.85,
                    'deltas': {'rewrite_hybrid_rerank': 0.0, 'accuracy_full_stack': -0.06},
                },
            },
        }

        gate = _build_release_gate(payload, max_latency_increase_ms=80.0, max_quality_regression=0.0)

        self.assertEqual(gate['status'], 'fail')
        self.assertIn('不建议直接切默认策略', gate['recommendation'])
        self.assertIn('`context_precision` 相对基线回退', ' '.join(gate['reasons']))


if __name__ == '__main__':
    unittest.main()

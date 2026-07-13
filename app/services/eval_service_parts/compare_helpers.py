"""`eval_service.py` 的对比指标与基础辅助子模块。

负责回放对比指标、任务持久化和默认策略构造，
把与主公共接口无关的公共辅助逻辑从主类中抽离。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.eval import (
    EvalStrategyConfig,
    EvalTaskResponse,
    ReplayCompareMetricItem,
    ReplayStrategySummary,
)
from app.services.eval_service_parts._typing import EvalServiceTypingMixin

if TYPE_CHECKING:
    pass


class EvalCompareMixin(EvalServiceTypingMixin):
    """放评测对比、任务状态查询和默认策略这些公共辅助逻辑。"""

    def _build_replay_compare_metrics(
        self,
        summaries: list[ReplayStrategySummary],
        baseline_name: str,
    ) -> tuple[dict[str, ReplayCompareMetricItem], dict[str, dict[str, ReplayCompareMetricItem]]]:
        """把回放结果整理成整体和分 bucket 两层对比指标。"""
        direction = {
            'success_rate': 'higher',
            'avg_retrieved_count': 'higher',
            'avg_latency_ms': 'lower',
        }

        values_by_strategy: dict[str, dict[str, float]] = {}
        bucket_values: dict[str, dict[str, dict[str, float]]] = {}
        for item in summaries:
            name = item.strategy.name
            sample_count = max(int(item.sample_count), 0)
            success_rate = (float(item.success_count) / sample_count) if sample_count else 0.0
            values_by_strategy[name] = {
                'success_rate': round(success_rate, 4),
                'avg_retrieved_count': float(item.avg_retrieved_count),
                'avg_latency_ms': float(item.avg_latency_ms),
            }
            for bucket, stats in (item.buckets or {}).items():
                bucket_sample = max(int(stats.sample_count), 0)
                bucket_success_rate = (float(stats.success_count) / bucket_sample) if bucket_sample else 0.0
                bucket_values.setdefault(bucket, {})[name] = {
                    'success_rate': round(bucket_success_rate, 4),
                    'avg_retrieved_count': float(stats.avg_retrieved_count),
                    'avg_latency_ms': float(stats.avg_latency_ms),
                }

        baseline_values = values_by_strategy.get(baseline_name, {})
        metrics: dict[str, ReplayCompareMetricItem] = {}
        for metric, mode in direction.items():
            candidates = {
                name: float(value)
                for name, values in values_by_strategy.items()
                for value in [values.get(metric)]
                if value is not None
            }
            if not candidates:
                continue
            baseline_value = baseline_values.get(metric)
            if mode == 'lower':
                best_strategy, best_value = min(candidates.items(), key=lambda item: item[1])
                deltas = {
                    name: round((float(baseline_value) - value), 4)
                    for name, value in candidates.items()
                    if baseline_value is not None
                }
            else:
                best_strategy, best_value = max(candidates.items(), key=lambda item: item[1])
                deltas = {
                    name: round((value - float(baseline_value)), 4)
                    for name, value in candidates.items()
                    if baseline_value is not None
                }
            metrics[metric] = ReplayCompareMetricItem(
                metric=metric,
                baseline=baseline_value,
                best_strategy=best_strategy,
                best_value=round(best_value, 4),
                deltas=deltas,
            )

        bucket_metrics: dict[str, dict[str, ReplayCompareMetricItem]] = {}
        for bucket, strategy_values in bucket_values.items():
            baseline_bucket = strategy_values.get(baseline_name, {})
            bucket_metrics[bucket] = {}
            for metric, mode in direction.items():
                candidates = {
                    name: float(value)
                    for name, values in strategy_values.items()
                    for value in [values.get(metric)]
                    if value is not None
                }
                if not candidates:
                    continue
                baseline_value = baseline_bucket.get(metric)
                if mode == 'lower':
                    best_strategy, best_value = min(candidates.items(), key=lambda item: item[1])
                    deltas = {
                        name: round((float(baseline_value) - value), 4)
                        for name, value in candidates.items()
                        if baseline_value is not None
                    }
                else:
                    best_strategy, best_value = max(candidates.items(), key=lambda item: item[1])
                    deltas = {
                        name: round((value - float(baseline_value)), 4)
                        for name, value in candidates.items()
                        if baseline_value is not None
                    }
                bucket_metrics[bucket][metric] = ReplayCompareMetricItem(
                    metric=metric,
                    baseline=baseline_value,
                    best_strategy=best_strategy,
                    best_value=round(best_value, 4),
                    deltas=deltas,
                )

        return metrics, bucket_metrics

    def get_task(self, task_id: str) -> EvalTaskResponse | None:
        """查询指定评测任务。"""
        payload = self.state.eval_tasks.get(task_id)
        if payload is None:
            return None
        return EvalTaskResponse(**payload)

    def get_runtime_status(self) -> dict:
        """返回评测依赖和鉴权配置现在能不能用。"""
        importable, reason = self._ragas_import_status()
        credentials_ready = bool(self.settings.resolved_llm_api_key and self.settings.resolved_embed_api_key)
        return {
            'importable': importable,
            'credentials_ready': credentials_ready,
            'ready': importable and credentials_ready,
            'reason': reason,
        }

    def build_default_compare_strategies(self, top_k: int = 5) -> list[EvalStrategyConfig]:
        """给前端或脚本一组开箱即用的默认评测策略。"""
        return [
            EvalStrategyConfig(
                name='rewrite_hybrid_rerank',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
            ),
            EvalStrategyConfig(
                name='parent_chunk_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_parent_chunk_retrieval=True,
            ),
            EvalStrategyConfig(
                name='question_oriented_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_question_oriented_index=True,
            ),
            EvalStrategyConfig(
                name='corrective_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_corrective_rag=True,
            ),
            EvalStrategyConfig(
                name='accuracy_full_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_parent_chunk_retrieval=True,
                use_question_oriented_index=True,
                use_corrective_rag=True,
            ),
            EvalStrategyConfig(
                name='graph_1hop_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_graph_rag=True,
                graph_max_hops=1,
                graph_top_k=top_k,
            ),
            EvalStrategyConfig(
                name='graph_2hop_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_graph_rag=True,
                graph_max_hops=2,
                graph_top_k=top_k,
            ),
            EvalStrategyConfig(
                name='accuracy_graph_full_stack',
                top_k=top_k,
                use_query_rewrite=True,
                use_hybrid_retrieval=True,
                use_rerank=True,
                use_parent_chunk_retrieval=True,
                use_question_oriented_index=True,
                use_corrective_rag=True,
                use_graph_rag=True,
                graph_max_hops=2,
                graph_top_k=top_k,
            ),
        ]

    def _persist_task(self, task: EvalTaskResponse) -> None:
        """把任务状态顺手同步到持久化层。"""
        if self.persistence is not None:
            self.persistence.upsert_eval_task(task.model_dump(mode='json'))

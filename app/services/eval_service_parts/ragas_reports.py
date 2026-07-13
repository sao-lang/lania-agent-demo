"""`eval_service.py` 的 Ragas 运行时与报告输出子模块。

负责 Ragas 依赖加载、模型构建、指标汇总、结果文件写入和策略回放，
把外部依赖和本地文件 IO 与主服务接口隔离开。
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Any

from app.core.errors import bad_request_error
from app.models.eval import (
    DocumentAnalysisBenchmarkResponse,
    EvalStrategyConfig,
    RagasCompareMetricItem,
    RagasCompareResponse,
    RagasCompareStrategyResult,
    RagasEvalRequest,
    ReplayBucketStats,
    ReplayCompareResponse,
    ReplayStrategySummary,
)
from app.models.query import QueryRequest
from app.services.eval_service_parts._typing import EvalServiceTypingMixin

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings


class EvalRagasReportMixin(EvalServiceTypingMixin):
    """放 Ragas 运行时依赖、结果写盘和策略回放这些实现细节。"""

    def _load_ragas_components(self) -> tuple[Any, list[Any]]:
        """延迟导入 Ragas 组件，避免应用启动时就因为评测依赖报错。"""
        self._install_ragas_import_compat()
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

        metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
        return evaluate, metrics

    def _build_ragas_models(self) -> tuple[ChatOpenAI, OpenAIEmbeddings]:
        """构建 Ragas 评测要用的 LLM 和 Embedding 模型。"""
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        if not self.settings.resolved_llm_api_key:
            raise ValueError('未配置 LLM API Key，无法执行 RAGAS 评测')
        if not self.settings.resolved_embed_api_key:
            raise ValueError('未配置 Embedding API Key，无法执行 RAGAS 评测')

        llm_kwargs: dict[str, Any] = {
            'model': self.settings.llm_model,
            'api_key': self.settings.resolved_llm_api_key,
            'timeout': self.settings.request_timeout_seconds,
        }
        if self.settings.resolved_llm_base_url:
            llm_kwargs['base_url'] = self.settings.resolved_llm_base_url

        embedding_kwargs: dict[str, Any] = {
            'model': self.settings.embed_model,
            'api_key': self.settings.resolved_embed_api_key,
        }
        if self.settings.resolved_embed_base_url:
            embedding_kwargs['base_url'] = self.settings.resolved_embed_base_url

        return ChatOpenAI(**llm_kwargs), OpenAIEmbeddings(**embedding_kwargs)

    def _summarize_metrics(self, scores: list[dict]) -> dict[str, float]:
        """按指标维度算均值，并统一保留四位小数。"""
        if not scores:
            return {}

        metrics: dict[str, list[float]] = {}
        for row in scores:
            for key, value in row.items():
                if value is None:
                    continue
                metrics.setdefault(key, []).append(float(value))

        return {key: round(mean(values), 4) for key, values in metrics.items() if values}

    def _write_result_file(
        self,
        task_id: str,
        payload: RagasEvalRequest,
        replay_details: list[dict],
        metric_summary: dict[str, float],
        scores: list[dict],
    ) -> Path:
        """把单次评测结果写到本地 JSON 文件。"""
        self.settings.eval_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.eval_dir / f'{task_id}.json'
        observability_summary = self._summarize_replay_observability(replay_details)
        target.write_text(
            json.dumps(
                {
                    'task_id': task_id,
                    'dataset_path': payload.dataset_path,
                    'collection_name': payload.collection_name,
                    'top_k': payload.top_k,
                    'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                    'use_rerank': payload.use_rerank,
                    'use_parent_chunk_retrieval': payload.use_parent_chunk_retrieval,
                    'use_question_oriented_index': payload.use_question_oriented_index,
                    'use_corrective_rag': payload.use_corrective_rag,
                    'use_graph_rag': payload.use_graph_rag,
                    'graph_max_hops': payload.graph_max_hops,
                    'graph_top_k': payload.graph_top_k,
                    'graph_entity_types': payload.graph_entity_types,
                    'metrics': metric_summary,
                    'observability_summary': observability_summary,
                    'scores': scores,
                    'samples': replay_details,
                    'generated_at': datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        return target

    def _write_compare_result_file(self, payload: RagasCompareResponse) -> Path:
        """把多策略对比结果写到本地 JSON 文件。"""
        self.settings.eval_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.eval_dir / f'{payload.compare_id}.json'
        target.write_text(
            json.dumps(payload.model_dump(mode='json'), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        return target

    def _write_replay_compare_result_file(self, payload: ReplayCompareResponse) -> Path:
        """把查询回放对比结果写到本地 JSON 文件。

        Args:
            payload: 查询回放对比响应对象。

        Returns:
            结果文件路径。
        """
        self.settings.eval_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.eval_dir / f'{payload.compare_id}.json'
        target.write_text(
            json.dumps(payload.model_dump(mode='json'), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        return target

    def _write_document_analysis_benchmark_result_file(self, payload: DocumentAnalysisBenchmarkResponse) -> Path:
        """把 Document Analysis benchmark 结果写到本地 JSON 文件。"""
        self.settings.eval_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.eval_dir / f'{payload.benchmark_id}.json'
        target.write_text(
            json.dumps(
                {
                    'report_mode': 'document_analysis_benchmark',
                    'dashboard_summary': (
                        payload.dashboard_summary.model_dump(mode='json') if payload.dashboard_summary is not None else None
                    ),
                    'gate': payload.gate.model_dump(mode='json') if payload.gate is not None else None,
                    'result': payload.model_dump(mode='json'),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        return target

    def _replay_strategy(
        self,
        dataset_entries: list[dict],
        default_collection_name: str,
        strategy: EvalStrategyConfig,
    ) -> ReplayStrategySummary:
        """按单个策略把整份数据集回放一遍，产出回放统计。"""
        bucket_flags: dict[str, list[tuple[int, int]]] = {}
        bucket_retrieved: dict[str, list[int]] = {}
        bucket_latency: dict[str, list[int]] = {}
        total_retrieved: list[int] = []
        total_latency: list[int] = []
        success_count = 0
        failed_count = 0

        for item in dataset_entries:
            bucket = str(item.get('bucket') or 'default')
            top_k = int(item.get('top_k') or strategy.top_k or 5)
            collection_name = item.get('collection_name') or default_collection_name
            try:
                # 回放时尽量吃样本自己的开关；样本没写时，再回退到当前策略默认值。
                response = self.query_service.query(
                    QueryRequest(
                        question=item['question'],
                        collection_name=collection_name,
                        top_k=top_k,
                        filters=item.get('filters'),
                        use_query_rewrite=item.get('use_query_rewrite', strategy.use_query_rewrite),
                        use_multi_query=item.get('use_multi_query', strategy.use_multi_query),
                        multi_query_count=item.get('multi_query_count', strategy.multi_query_count),
                        use_multi_rewrite=item.get('use_multi_rewrite', strategy.use_multi_rewrite),
                        multi_rewrite_count=item.get('multi_rewrite_count', strategy.multi_rewrite_count),
                        use_hybrid_retrieval=item.get('use_hybrid_retrieval', strategy.use_hybrid_retrieval),
                        use_rerank=item.get('use_rerank', strategy.use_rerank),
                        use_hyde=item.get('use_hyde', strategy.use_hyde),
                        use_long_context_reorder=item.get('use_long_context_reorder', strategy.use_long_context_reorder),
                        use_parent_chunk_retrieval=item.get(
                            'use_parent_chunk_retrieval',
                            strategy.use_parent_chunk_retrieval,
                        ),
                        use_question_oriented_index=item.get(
                            'use_question_oriented_index',
                            strategy.use_question_oriented_index,
                        ),
                        use_corrective_rag=item.get('use_corrective_rag', strategy.use_corrective_rag),
                        use_graph_rag=item.get('use_graph_rag', strategy.use_graph_rag),
                        graph_max_hops=item.get('graph_max_hops', strategy.graph_max_hops),
                        graph_top_k=item.get('graph_top_k', strategy.graph_top_k or top_k),
                        graph_entity_types=item.get('graph_entity_types', strategy.graph_entity_types),
                    )
                )
                retrieved = int(response.retrieved_count)
                latency_ms = int(response.latency_ms)
                total_retrieved.append(retrieved)
                total_latency.append(latency_ms)
                bucket_retrieved.setdefault(bucket, []).append(retrieved)
                bucket_latency.setdefault(bucket, []).append(latency_ms)
                if retrieved > 0:
                    success_count += 1
                    bucket_flags.setdefault(bucket, []).append((1, 0))
                else:
                    failed_count += 1
                    bucket_flags.setdefault(bucket, []).append((0, 1))
            except Exception:
                failed_count += 1
                bucket_flags.setdefault(bucket, []).append((0, 1))

        buckets: dict[str, ReplayBucketStats] = {}
        for bucket, flags in bucket_flags.items():
            ok = sum(pair[0] for pair in flags)
            bad = sum(pair[1] for pair in flags)
            retrieved_values = bucket_retrieved.get(bucket, [])
            latency_values = bucket_latency.get(bucket, [])
            buckets[bucket] = ReplayBucketStats(
                bucket=bucket,
                sample_count=len(flags),
                success_count=ok,
                failed_count=bad,
                avg_retrieved_count=round(mean(retrieved_values), 4) if retrieved_values else 0.0,
                avg_latency_ms=round(mean(latency_values), 4) if latency_values else 0.0,
            )

        return ReplayStrategySummary(
            strategy=strategy,
            sample_count=len(dataset_entries),
            success_count=success_count,
            failed_count=failed_count,
            avg_retrieved_count=round(mean(total_retrieved), 4) if total_retrieved else 0.0,
            avg_latency_ms=round(mean(total_latency), 4) if total_latency else 0.0,
            buckets=buckets,
        )

    def _validate_compare_strategies(
        self,
        strategies: list[EvalStrategyConfig],
        baseline_name: str | None,
    ) -> None:
        """校验对比策略数量、名称和基线配置。"""
        if len(strategies) < 2:
            raise bad_request_error(
                code='compare_strategies_too_few',
                message='至少需要两组策略才能进行对比评测',
            )

        names = [item.name.strip() for item in strategies]
        if any(not name for name in names):
            raise bad_request_error(
                code='compare_strategy_name_required',
                message='策略名称不能为空',
            )
        if len(set(names)) != len(names):
            raise bad_request_error(
                code='compare_strategy_name_duplicated',
                message='策略名称不能重复',
                details={'strategy_names': names},
            )
        if baseline_name and baseline_name not in names:
            raise bad_request_error(
                code='compare_baseline_not_found',
                message='baseline_name 必须对应已声明的策略名称',
                details={'baseline_name': baseline_name, 'strategy_names': names},
            )

    def _build_compare_metrics(
        self,
        results: list[RagasCompareStrategyResult],
        baseline_name: str,
    ) -> dict[str, RagasCompareMetricItem]:
        """把多策略任务结果整理成按指标聚合的对比视图。"""
        strategy_metrics = {
            item.strategy.name: item.task.metrics
            for item in results
            if item.task.status == 'completed'
        }
        baseline_metrics = strategy_metrics.get(baseline_name, {})
        metric_names = sorted({metric for item in strategy_metrics.values() for metric in item})
        comparison: dict[str, RagasCompareMetricItem] = {}

        for metric in metric_names:
            values = {
                name: metrics[metric]
                for name, metrics in strategy_metrics.items()
                if metric in metrics
            }
            if not values:
                continue
            best_strategy, best_value = max(values.items(), key=lambda item: item[1])
            baseline_value = baseline_metrics.get(metric)
            deltas = {
                name: round(value - baseline_value, 4)
                for name, value in values.items()
                if baseline_value is not None
            }
            comparison[metric] = RagasCompareMetricItem(
                metric=metric,
                baseline=baseline_value,
                best_strategy=best_strategy,
                best_value=round(best_value, 4),
                deltas=deltas,
            )
        return comparison

    def _ragas_import_status(self) -> tuple[bool, str | None]:
        """检查 Ragas 依赖现在能不能正常导入。"""
        try:
            self._install_ragas_import_compat()
            import ragas  # noqa: F401
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _install_ragas_import_compat(self) -> None:
        """安装兼容补丁，绕开一部分旧依赖导入链的问题。"""
        if 'langchain_community.chat_models.vertexai' in sys.modules:
            return

        module = types.ModuleType('langchain_community.chat_models.vertexai')

        class ChatVertexAI:  # pragma: no cover - compatibility shim
            """占位类，用于满足旧版本依赖的导入路径要求。"""

        setattr(module, 'ChatVertexAI', ChatVertexAI)
        sys.modules['langchain_community.chat_models.vertexai'] = module

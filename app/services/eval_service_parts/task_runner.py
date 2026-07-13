"""`eval_service.py` 的任务执行与查询回放子模块。

集中实现评测任务运行、数据集装载、查询回放与可观测信息归纳，
避免主类同时承载公开 API 与大量执行细节。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Any, cast

from app.core.bucketing import infer_bucket
from app.models.eval import (
    EvalTaskResponse,
    RagasEvalRequest,
)
from app.models.query import QueryRequest
from app.services.eval_service_parts._typing import EvalServiceTypingMixin

if TYPE_CHECKING:
    pass


class EvalTaskRunnerMixin(EvalServiceTypingMixin):
    """承接评测任务执行、数据集回放和结果落盘逻辑。"""

    def _run_task(self, task_id: str, payload: RagasEvalRequest) -> EvalTaskResponse:
        """回放评测集、执行 Ragas 计算并落盘结果。"""
        dataset_entries = self._load_eval_dataset(
            payload.dataset_path,
            payload.collection_name,
            payload.use_query_rewrite,
            payload.use_multi_query,
            payload.multi_query_count,
            payload.use_multi_rewrite,
            payload.multi_rewrite_count,
            payload.use_hybrid_retrieval,
            payload.use_hyde,
            payload.use_long_context_reorder,
            payload.use_parent_chunk_retrieval,
            payload.use_question_oriented_index,
            payload.use_corrective_rag,
            payload.use_graph_rag,
            payload.graph_max_hops,
            payload.graph_top_k,
            payload.graph_entity_types,
        )
        # 先回放线上查询链路，构造 Ragas 所需的 response 与 retrieved_contexts。
        replay_rows, replay_details = self._replay_queries(dataset_entries, payload)
        if not replay_rows:
            raise ValueError('评测集没有可用于 RAGAS 的有效样本')

        evaluate, metrics = self._load_ragas_components()
        llm, embeddings = self._build_ragas_models()
        from datasets import Dataset

        # 使用回放出的数据集执行指标计算。
        result = evaluate(
            Dataset.from_list(replay_rows),
            metrics=metrics,
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=False,
            show_progress=False,
        )
        scores = cast(list[dict[str, Any]], getattr(result, 'scores'))
        metric_summary = self._summarize_metrics(scores)
        result_path = self._write_result_file(task_id, payload, replay_details, metric_summary, scores)
        completed_at = datetime.now(timezone.utc)

        summary = (
            f'RAGAS 评测完成，共 {len(dataset_entries)} 条样本，'
            f'成功 {len(replay_rows)} 条，失败 {len(dataset_entries) - len(replay_rows)} 条。'
        )
        self.trace.record(
            'ragas_task_completed',
            {
                'task_id': task_id,
                'sample_count': len(dataset_entries),
                'success_count': len(replay_rows),
                'failed_count': len(dataset_entries) - len(replay_rows),
                'metrics': metric_summary,
            },
        )
        return EvalTaskResponse(
            task_id=task_id,
            status='completed',
            summary=summary,
            dataset_path=payload.dataset_path,
            collection_name=payload.collection_name,
            sample_count=len(dataset_entries),
            success_count=len(replay_rows),
            failed_count=len(dataset_entries) - len(replay_rows),
            metrics=metric_summary,
            result_path=str(result_path),
            started_at=EvalTaskResponse(**self.state.eval_tasks[task_id]).started_at,
            completed_at=completed_at,
        )

    def _load_eval_dataset(
        self,
        dataset_path: str,
        default_collection_name: str,
        default_use_query_rewrite: bool,
        default_use_multi_query: bool,
        default_multi_query_count: int,
        default_use_multi_rewrite: bool,
        default_multi_rewrite_count: int,
        default_use_hybrid_retrieval: bool,
        default_use_hyde: bool,
        default_use_long_context_reorder: bool,
        default_use_parent_chunk_retrieval: bool,
        default_use_question_oriented_index: bool,
        default_use_corrective_rag: bool,
        default_use_graph_rag: bool = False,
        default_graph_max_hops: int = 1,
        default_graph_top_k: int = 5,
        default_graph_entity_types: list[str] | None = None,
    ) -> list[dict]:
        """读取并校验评测数据集 JSON。

        同时把每条样本补成统一结构，后面回放时就不用再到处做兜底判断。
        """
        path = Path(dataset_path)
        if not path.exists():
            raise FileNotFoundError(f'评测集不存在: {dataset_path}')

        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, list):
            raise ValueError('评测集必须是 JSON 数组')

        dataset_entries: list[dict] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f'第 {index} 条样本不是对象')

            question = str(item.get('question', '')).strip()
            reference = str(item.get('ground_truth') or item.get('reference') or '').strip()
            if not question or not reference:
                raise ValueError(f'第 {index} 条样本缺少 question 或 ground_truth/reference')

            dataset_entries.append(
                {
                    'question': question,
                    'reference': reference,
                    'bucket': infer_bucket(
                        question,
                        reference,
                        metadata=item,
                    )
                    if not (item.get('bucket') or item.get('category') or item.get('type'))
                    else str(item.get('bucket') or item.get('category') or item.get('type')),
                    'eval_tags': item.get('tags'),
                    'collection_name': item.get('collection_name') or default_collection_name,
                    'top_k': int(item.get('top_k') or 5),
                    'filters': item.get('filters'),
                    'use_query_rewrite': bool(item.get('use_query_rewrite', default_use_query_rewrite)),
                    'use_multi_query': bool(item.get('use_multi_query', default_use_multi_query)),
                    'multi_query_count': int(item.get('multi_query_count', default_multi_query_count) or 3),
                    'use_multi_rewrite': bool(item.get('use_multi_rewrite', default_use_multi_rewrite)),
                    'multi_rewrite_count': int(item.get('multi_rewrite_count', default_multi_rewrite_count) or 3),
                    'use_hybrid_retrieval': bool(item.get('use_hybrid_retrieval', default_use_hybrid_retrieval)),
                    'use_rerank': bool(item.get('use_rerank', True)),
                    'use_hyde': bool(item.get('use_hyde', default_use_hyde)),
                    'use_long_context_reorder': bool(
                        item.get('use_long_context_reorder', default_use_long_context_reorder)
                    ),
                    'use_parent_chunk_retrieval': bool(
                        item.get('use_parent_chunk_retrieval', default_use_parent_chunk_retrieval)
                    ),
                    'use_question_oriented_index': bool(
                        item.get('use_question_oriented_index', default_use_question_oriented_index)
                    ),
                    'use_corrective_rag': bool(item.get('use_corrective_rag', default_use_corrective_rag)),
                    'use_graph_rag': bool(item.get('use_graph_rag', default_use_graph_rag)),
                    'graph_max_hops': int(item.get('graph_max_hops', default_graph_max_hops) or 1),
                    'graph_top_k': int(item.get('graph_top_k', default_graph_top_k) or 5),
                    'graph_entity_types': item.get('graph_entity_types', default_graph_entity_types),
                    'metadata': item,
                }
            )
        return dataset_entries

    def _replay_queries(self, dataset_entries: list[dict], payload: RagasEvalRequest) -> tuple[list[dict], list[dict]]:
        """逐条执行查询，生成评测输入和详细回放记录。"""
        replay_rows: list[dict] = []
        replay_details: list[dict] = []

        for index, item in enumerate(dataset_entries, start=1):
            trace_start = len(self.trace.events)
            try:
                # 这里直接复用真实 QueryService，评测跑出来的结果才和线上链路尽量一致。
                response = self.query_service.query(
                    QueryRequest(
                        question=item['question'],
                        collection_name=item['collection_name'],
                        top_k=item['top_k'] or payload.top_k,
                        filters=item.get('filters'),
                        use_query_rewrite=item.get('use_query_rewrite', payload.use_query_rewrite),
                        use_multi_query=item.get('use_multi_query', payload.use_multi_query),
                        multi_query_count=item.get('multi_query_count', payload.multi_query_count),
                        use_multi_rewrite=item.get('use_multi_rewrite', payload.use_multi_rewrite),
                        multi_rewrite_count=item.get('multi_rewrite_count', payload.multi_rewrite_count),
                        use_hybrid_retrieval=item.get('use_hybrid_retrieval', payload.use_hybrid_retrieval),
                        use_rerank=item.get('use_rerank', payload.use_rerank),
                        use_hyde=item.get('use_hyde', payload.use_hyde),
                        use_long_context_reorder=item.get('use_long_context_reorder', payload.use_long_context_reorder),
                        use_parent_chunk_retrieval=item.get(
                            'use_parent_chunk_retrieval',
                            payload.use_parent_chunk_retrieval,
                        ),
                        use_question_oriented_index=item.get(
                            'use_question_oriented_index',
                            payload.use_question_oriented_index,
                        ),
                        use_corrective_rag=item.get('use_corrective_rag', payload.use_corrective_rag),
                        use_graph_rag=item.get('use_graph_rag', payload.use_graph_rag),
                        graph_max_hops=item.get('graph_max_hops', payload.graph_max_hops),
                        graph_top_k=item.get('graph_top_k', payload.graph_top_k),
                        graph_entity_types=item.get('graph_entity_types', payload.graph_entity_types),
                    )
                )
                retrieved_contexts = [citation.text for citation in response.citations]
                if not retrieved_contexts:
                    raise ValueError('检索结果为空')
                observability = self._build_sample_observability(response, trace_start)

                replay_rows.append(
                    {
                        'user_input': item['question'],
                        'response': response.answer,
                        'retrieved_contexts': retrieved_contexts,
                        'reference': item['reference'],
                    }
                )
                replay_details.append(
                    {
                        'index': index,
                        'question': item['question'],
                        'reference': item['reference'],
                        'use_query_rewrite': item.get('use_query_rewrite', payload.use_query_rewrite),
                        'use_multi_query': item.get('use_multi_query', payload.use_multi_query),
                        'multi_query_count': item.get('multi_query_count', payload.multi_query_count),
                        'use_multi_rewrite': item.get('use_multi_rewrite', payload.use_multi_rewrite),
                        'multi_rewrite_count': item.get('multi_rewrite_count', payload.multi_rewrite_count),
                        'use_hybrid_retrieval': item.get('use_hybrid_retrieval', payload.use_hybrid_retrieval),
                        'use_rerank': item.get('use_rerank', payload.use_rerank),
                        'use_hyde': item.get('use_hyde', payload.use_hyde),
                        'use_long_context_reorder': item.get('use_long_context_reorder', payload.use_long_context_reorder),
                        'use_parent_chunk_retrieval': item.get(
                            'use_parent_chunk_retrieval',
                            payload.use_parent_chunk_retrieval,
                        ),
                        'use_question_oriented_index': item.get(
                            'use_question_oriented_index',
                            payload.use_question_oriented_index,
                        ),
                        'use_corrective_rag': item.get('use_corrective_rag', payload.use_corrective_rag),
                        'use_graph_rag': item.get('use_graph_rag', payload.use_graph_rag),
                        'graph_max_hops': item.get('graph_max_hops', payload.graph_max_hops),
                        'graph_top_k': item.get('graph_top_k', payload.graph_top_k),
                        'graph_entity_types': item.get('graph_entity_types', payload.graph_entity_types),
                        'response': response.answer,
                        'retrieved_contexts': retrieved_contexts,
                        'citations': [citation.model_dump() for citation in response.citations],
                        'observability': observability,
                        'latency_ms': response.latency_ms,
                        'status': 'ok',
                    }
                )
            except Exception as exc:
                # 单条样本失败只记失败明细，不中断整批评测，方便最后统一看成功率和失败原因。
                observability = self._build_failed_sample_observability(trace_start)
                replay_details.append(
                    {
                        'index': index,
                        'question': item['question'],
                        'reference': item['reference'],
                        'use_query_rewrite': item.get('use_query_rewrite', payload.use_query_rewrite),
                        'use_multi_query': item.get('use_multi_query', payload.use_multi_query),
                        'multi_query_count': item.get('multi_query_count', payload.multi_query_count),
                        'use_multi_rewrite': item.get('use_multi_rewrite', payload.use_multi_rewrite),
                        'multi_rewrite_count': item.get('multi_rewrite_count', payload.multi_rewrite_count),
                        'use_hybrid_retrieval': item.get('use_hybrid_retrieval', payload.use_hybrid_retrieval),
                        'use_rerank': item.get('use_rerank', payload.use_rerank),
                        'use_hyde': item.get('use_hyde', payload.use_hyde),
                        'use_long_context_reorder': item.get('use_long_context_reorder', payload.use_long_context_reorder),
                        'use_parent_chunk_retrieval': item.get(
                            'use_parent_chunk_retrieval',
                            payload.use_parent_chunk_retrieval,
                        ),
                        'use_question_oriented_index': item.get(
                            'use_question_oriented_index',
                            payload.use_question_oriented_index,
                        ),
                        'use_corrective_rag': item.get('use_corrective_rag', payload.use_corrective_rag),
                        'use_graph_rag': item.get('use_graph_rag', payload.use_graph_rag),
                        'graph_max_hops': item.get('graph_max_hops', payload.graph_max_hops),
                        'graph_top_k': item.get('graph_top_k', payload.graph_top_k),
                        'graph_entity_types': item.get('graph_entity_types', payload.graph_entity_types),
                        'observability': observability,
                        'status': 'failed',
                        'error': str(exc),
                    }
                )
        return replay_rows, replay_details

    def _build_sample_observability(self, response: Any, trace_start: int) -> dict[str, Any]:
        """整理单条样本的检索增强、切块命中和缓存观测信息。"""
        trace_events = self.trace.events[trace_start:]
        retrieval_payload = self._latest_trace_payload(trace_events, {'retrieval', 'retrieval_multi'})
        query_payload = self._latest_trace_payload(trace_events, {'query_completed'})

        matched_via_breakdown: dict[str, int] = {}
        matched_via_union: list[str] = []
        matched_via_seen: set[str] = set()
        index_kind_breakdown: dict[str, int] = {}
        context_scope_breakdown: dict[str, int] = {}
        semantic_prepared_hit_count = 0
        semantic_effective_hit_count = 0
        fixed_effective_hit_count = 0
        source_segment_counts: list[int] = []

        for citation in response.citations:
            for marker in citation.matched_via or []:
                name = str(marker).strip()
                if not name:
                    continue
                matched_via_breakdown[name] = matched_via_breakdown.get(name, 0) + 1
                if name not in matched_via_seen:
                    matched_via_union.append(name)
                    matched_via_seen.add(name)
            if citation.index_kind:
                index_kind_breakdown[citation.index_kind] = index_kind_breakdown.get(citation.index_kind, 0) + 1
            if citation.context_scope:
                context_scope_breakdown[citation.context_scope] = context_scope_breakdown.get(citation.context_scope, 0) + 1
            if citation.chunking_prepared:
                semantic_prepared_hit_count += 1
            effective_strategy = str(citation.chunking_strategy_effective or '').strip().lower()
            if effective_strategy == 'semantic':
                semantic_effective_hit_count += 1
            elif effective_strategy == 'fixed':
                fixed_effective_hit_count += 1
            if citation.source_segment_count:
                source_segment_counts.append(int(citation.source_segment_count))

        post_aggregate = retrieval_payload.get('post_aggregate') or []
        parent_info = retrieval_payload.get('parent_chunk') or {}
        graph_info = retrieval_payload.get('graph') or {}
        context_compression = query_payload.get('context_compression') or {}
        semantic_cache = query_payload.get('semantic_cache') or {}

        return {
            'matched_via_union': matched_via_union,
            'matched_via_breakdown': matched_via_breakdown,
            'index_kind_breakdown': index_kind_breakdown,
            'context_scope_breakdown': context_scope_breakdown,
            'any_semantic_prepared_hit': semantic_prepared_hit_count > 0,
            'semantic_prepared_hit_count': semantic_prepared_hit_count,
            'semantic_effective_hit_count': semantic_effective_hit_count,
            'fixed_effective_hit_count': fixed_effective_hit_count,
            'max_source_segment_count': max(source_segment_counts) if source_segment_counts else 0,
            'avg_source_segment_count': round(mean(source_segment_counts), 4) if source_segment_counts else 0.0,
            'retrieval': {
                'retrieval_mode': retrieval_payload.get('retrieval_mode'),
                'rerank_mode': retrieval_payload.get('rerank_mode'),
                'aggregated_targets': len(post_aggregate),
                'aggregated_away_candidates': max(len(retrieval_payload.get('pre_rerank') or []) - len(post_aggregate), 0),
                'multi_vector_hits': sum(
                    1
                    for item in post_aggregate
                    if len([str(marker).strip() for marker in item.get('matched_via') or [] if str(marker).strip()]) >= 2
                ),
                'parent_expanded': int(parent_info.get('expanded', 0)),
                'parent_document_hits': int(parent_info.get('parent_document_hits', 0)),
                'graph_enabled': bool(retrieval_payload.get('use_graph_rag')),
                'graph_candidates': int(retrieval_payload.get('graph_candidates', 0) or 0),
                'graph_seed_node_count': int(graph_info.get('seed_node_count', 0) or 0),
                'graph_expanded_edge_count': int(graph_info.get('expanded_edge_count', 0) or 0),
                'graph_returned_citations': int(graph_info.get('returned_citations', 0) or 0),
            },
            'query': {
                'answer_mode': query_payload.get('answer_mode'),
                'use_context_compression': bool(query_payload.get('use_context_compression')),
                'compressed_chunk_count': int(context_compression.get('compressed_chunk_count', 0) or 0),
                'compressed_char_count': int(context_compression.get('compressed_char_count', 0) or 0),
                'semantic_cache_hit': bool(semantic_cache.get('hit')),
                'semantic_cache_match_type': semantic_cache.get('match_type'),
            },
        }

    def _build_failed_sample_observability(self, trace_start: int) -> dict[str, Any]:
        """给失败样本补一份尽量完整但字段更保守的链路观测信息。"""
        trace_events = self.trace.events[trace_start:]
        retrieval_payload = self._latest_trace_payload(trace_events, {'retrieval', 'retrieval_multi'})
        query_payload = self._latest_trace_payload(trace_events, {'query_completed'})
        return {
            'matched_via_union': [],
            'matched_via_breakdown': {},
            'index_kind_breakdown': {},
            'context_scope_breakdown': {},
            'any_semantic_prepared_hit': False,
            'semantic_prepared_hit_count': 0,
            'semantic_effective_hit_count': 0,
            'fixed_effective_hit_count': 0,
            'max_source_segment_count': 0,
            'avg_source_segment_count': 0.0,
            'retrieval': {
                'retrieval_mode': retrieval_payload.get('retrieval_mode'),
                'rerank_mode': retrieval_payload.get('rerank_mode'),
                'aggregated_targets': len(retrieval_payload.get('post_aggregate') or []),
                'aggregated_away_candidates': max(
                    len(retrieval_payload.get('pre_rerank') or []) - len(retrieval_payload.get('post_aggregate') or []),
                    0,
                ),
                'multi_vector_hits': 0,
                'parent_expanded': int((retrieval_payload.get('parent_chunk') or {}).get('expanded', 0)),
                'parent_document_hits': int((retrieval_payload.get('parent_chunk') or {}).get('parent_document_hits', 0)),
                'graph_enabled': bool(retrieval_payload.get('use_graph_rag')),
                'graph_candidates': int(retrieval_payload.get('graph_candidates', 0) or 0),
                'graph_seed_node_count': int((retrieval_payload.get('graph') or {}).get('seed_node_count', 0) or 0),
                'graph_expanded_edge_count': int((retrieval_payload.get('graph') or {}).get('expanded_edge_count', 0) or 0),
                'graph_returned_citations': int((retrieval_payload.get('graph') or {}).get('returned_citations', 0) or 0),
            },
            'query': {
                'answer_mode': query_payload.get('answer_mode'),
                'use_context_compression': bool(query_payload.get('use_context_compression')),
                'compressed_chunk_count': int((query_payload.get('context_compression') or {}).get('compressed_chunk_count', 0) or 0),
                'compressed_char_count': int((query_payload.get('context_compression') or {}).get('compressed_char_count', 0) or 0),
                'semantic_cache_hit': bool((query_payload.get('semantic_cache') or {}).get('hit')),
                'semantic_cache_match_type': (query_payload.get('semantic_cache') or {}).get('match_type'),
            },
        }

    def _latest_trace_payload(self, trace_events: list[Any], names: set[str]) -> dict[str, Any]:
        """从当前 trace 窗口里拿到最后一条匹配事件的 payload。"""
        for event in reversed(trace_events):
            if event.name in names:
                return dict(event.payload)
        return {}

    def _summarize_replay_observability(self, replay_details: list[dict[str, Any]]) -> dict[str, Any]:
        """把单次评测里每条样本的观测信息压成一份汇总。"""
        matched_via_breakdown: dict[str, int] = {}
        multi_vector_samples = 0
        semantic_prepared_samples = 0
        parent_document_hits = 0
        graph_samples = 0
        graph_seed_nodes = 0
        graph_expanded_edges = 0
        context_compression_samples = 0
        semantic_cache_hit_samples = 0

        for item in replay_details:
            observability = item.get('observability') or {}
            matched_via = observability.get('matched_via_breakdown') or {}
            for key, value in matched_via.items():
                matched_via_breakdown[str(key)] = matched_via_breakdown.get(str(key), 0) + int(value)
            retrieval = observability.get('retrieval') or {}
            query = observability.get('query') or {}
            if int(retrieval.get('multi_vector_hits', 0) or 0) > 0:
                multi_vector_samples += 1
            if observability.get('any_semantic_prepared_hit'):
                semantic_prepared_samples += 1
            parent_document_hits += int(retrieval.get('parent_document_hits', 0) or 0)
            if retrieval.get('graph_enabled'):
                graph_samples += 1
            graph_seed_nodes += int(retrieval.get('graph_seed_node_count', 0) or 0)
            graph_expanded_edges += int(retrieval.get('graph_expanded_edge_count', 0) or 0)
            if query.get('use_context_compression'):
                context_compression_samples += 1
            if query.get('semantic_cache_hit'):
                semantic_cache_hit_samples += 1

        return {
            'sample_count': len(replay_details),
            'multi_vector_samples': multi_vector_samples,
            'semantic_prepared_samples': semantic_prepared_samples,
            'parent_document_hits': parent_document_hits,
            'graph_samples': graph_samples,
            'graph_seed_nodes': graph_seed_nodes,
            'graph_expanded_edges': graph_expanded_edges,
            'context_compression_samples': context_compression_samples,
            'semantic_cache_hit_samples': semantic_cache_hit_samples,
            'matched_via_breakdown': matched_via_breakdown,
        }

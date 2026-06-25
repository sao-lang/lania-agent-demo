"""轻量级可观测性模块。

负责把检索、压缩、缓存、任务工作流等关键阶段的事件记录到内存缓冲区，并提供若干
聚合统计方法，便于接口层或调试工具快速查看系统近期运行质量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, TypedDict, cast


@dataclass
class TraceEvent:
    """描述一次被记录的链路事件。"""

    name: str
    payload: dict
    timestamp: float = field(default_factory=time)


class TraceRecorder:
    """应用内存态 trace 记录器。

    这一层不追求复杂链路系统，只是先把关键事件攒在内存里，方便接口层、benchmark 和调试脚本
    直接读最近一段运行情况。
    """

    def __init__(self) -> None:
        """初始化空的事件缓冲区。"""
        self.events: list[TraceEvent] = []

    def record(self, name: str, payload: dict) -> None:
        """记录一条带名称和载荷的追踪事件。

        Args:
            name: 事件名称，例如 `retrieval`、`semantic_cache_lookup`。
            payload: 事件附带的结构化上下文字段。
        """
        self.events.append(TraceEvent(name=name, payload=payload))

    def summarize_context_compression(self, last_n: int | None = None) -> dict:
        """聚合上下文压缩的累计或最近窗口效果指标。

        Args:
            last_n: 仅统计最近 N 条相关事件；为空时统计全部。

        Returns:
            上下文压缩效果的聚合指标字典。
        """
        compression_events = [event.payload for event in self.events if event.name == 'context_compressed']
        if last_n is not None and last_n > 0:
            compression_events = compression_events[-last_n:]
        if not compression_events:
            return {
                'compressed_requests': 0,
                'avg_char_reduction_ratio': 0.0,
                'avg_sentence_reduction_ratio': 0.0,
                'avg_chunk_reduction_ratio': 0.0,
                'total_original_char_count': 0,
                'total_compressed_char_count': 0,
                'total_original_sentence_count': 0,
                'total_compressed_sentence_count': 0,
                'total_original_chunk_count': 0,
                'total_compressed_chunk_count': 0,
                'strategy_breakdown': {},
            }

        total_original_chars = sum(int(item.get('original_char_count', 0)) for item in compression_events)
        total_compressed_chars = sum(int(item.get('compressed_char_count', 0)) for item in compression_events)
        total_original_sentences = sum(int(item.get('original_sentence_count', 0)) for item in compression_events)
        total_compressed_sentences = sum(int(item.get('compressed_sentence_count', 0)) for item in compression_events)
        total_original_chunks = sum(int(item.get('original_chunk_count', 0)) for item in compression_events)
        total_compressed_chunks = sum(int(item.get('compressed_chunk_count', 0)) for item in compression_events)

        def _reduction_ratio(original: int, compressed: int) -> float:
            if original <= 0:
                return 0.0
            return round(max(original - compressed, 0) / original, 4)

        strategy_breakdown: dict[str, int] = {}
        for item in compression_events:
            strategy = str(item.get('strategy') or 'unknown')
            strategy_breakdown[strategy] = strategy_breakdown.get(strategy, 0) + 1

        return {
            'compressed_requests': len(compression_events),
            'avg_char_reduction_ratio': _reduction_ratio(total_original_chars, total_compressed_chars),
            'avg_sentence_reduction_ratio': _reduction_ratio(total_original_sentences, total_compressed_sentences),
            'avg_chunk_reduction_ratio': _reduction_ratio(total_original_chunks, total_compressed_chunks),
            'total_original_char_count': total_original_chars,
            'total_compressed_char_count': total_compressed_chars,
            'total_original_sentence_count': total_original_sentences,
            'total_compressed_sentence_count': total_compressed_sentences,
            'total_original_chunk_count': total_original_chunks,
            'total_compressed_chunk_count': total_compressed_chunks,
            'strategy_breakdown': strategy_breakdown,
        }

    def summarize_semantic_cache(self, last_n: int | None = None) -> dict:
        """聚合语义缓存命中、写入和失效情况。

        Args:
            last_n: 仅统计最近 N 条相关事件；为空时统计全部。

        Returns:
            语义缓存命中率、写入情况和失效统计。
        """
        lookup_events = [event.payload for event in self.events if event.name == 'semantic_cache_lookup']
        if last_n is not None and last_n > 0:
            lookup_events = lookup_events[-last_n:]
        store_events = [event.payload for event in self.events if event.name == 'semantic_cache_store']
        invalidate_events = [event.payload for event in self.events if event.name == 'semantic_cache_invalidate']
        if last_n is not None and last_n > 0:
            store_events = store_events[-last_n:]
            invalidate_events = invalidate_events[-last_n:]

        hits = [item for item in lookup_events if item.get('hit')]
        misses = [item for item in lookup_events if not item.get('hit')]
        hit_similarities = [float(item.get('similarity', 0.0)) for item in hits]
        total_lookups = len(lookup_events)
        match_breakdown: dict[str, int] = {}
        miss_reason_breakdown: dict[str, int] = {}
        for item in hits:
            match_type = str(item.get('match_type') or 'unknown')
            match_breakdown[match_type] = match_breakdown.get(match_type, 0) + 1
        for item in misses:
            reason = str(item.get('reason') or 'unknown')
            miss_reason_breakdown[reason] = miss_reason_breakdown.get(reason, 0) + 1

        return {
            'lookups': total_lookups,
            'hits': len(hits),
            'misses': len(misses),
            'hit_rate': round(len(hits) / total_lookups, 4) if total_lookups else 0.0,
            'avg_hit_similarity': round(sum(hit_similarities) / len(hit_similarities), 4) if hit_similarities else 0.0,
            'writes': sum(1 for item in store_events if item.get('stored')),
            'write_skips': sum(1 for item in store_events if not item.get('stored')),
            'invalidations': len(invalidate_events),
            'invalidated_entries': sum(int(item.get('invalidated_entries', 0)) for item in invalidate_events),
            'match_breakdown': match_breakdown,
            'miss_reason_breakdown': miss_reason_breakdown,
        }

    def summarize_semantic_chunking(self, last_n: int | None = None) -> dict:
        """聚合语义切块预处理阶段的归并和保留情况。

        Args:
            last_n: 仅统计最近 N 条相关事件；为空时统计全部。

        Returns:
            语义切块预处理阶段的聚合统计。
        """
        events = [event.payload for event in self.events if event.name == 'semantic_chunking_prepared']
        if last_n is not None and last_n > 0:
            events = events[-last_n:]
        if not events:
            return {
                'documents': 0,
                'requested_semantic_documents': 0,
                'source_segments': 0,
                'prepared_segments': 0,
                'semantic_segments': 0,
                'fixed_segments': 0,
                'prepared_groups': 0,
                'merged_source_segments': 0,
                'avg_merge_ratio': 0.0,
            }

        documents = len(events)
        source_segments = sum(int(item.get('source_segments', 0)) for item in events)
        prepared_segments = sum(int(item.get('prepared_segments', 0)) for item in events)
        semantic_segments = sum(int(item.get('semantic_segments', 0)) for item in events)
        fixed_segments = sum(int(item.get('fixed_segments', 0)) for item in events)
        prepared_groups = sum(int(item.get('prepared_groups', 0)) for item in events)
        merged_source_segments = sum(int(item.get('merged_source_segments', 0)) for item in events)
        requested_semantic_documents = sum(
            1 for item in events if str(item.get('requested_strategy') or '').strip().lower() == 'semantic'
        )
        return {
            'documents': documents,
            'requested_semantic_documents': requested_semantic_documents,
            'source_segments': source_segments,
            'prepared_segments': prepared_segments,
            'semantic_segments': semantic_segments,
            'fixed_segments': fixed_segments,
            'prepared_groups': prepared_groups,
            'merged_source_segments': merged_source_segments,
            'avg_merge_ratio': round(merged_source_segments / source_segments, 4) if source_segments else 0.0,
        }

    def summarize_retrieval_enhancements(self, last_n: int | None = None) -> dict:
        """聚合多向量命中、目标块聚合和父文档回填效果。

        Args:
            last_n: 仅统计最近 N 条相关事件；为空时统计全部。

        Returns:
            检索增强策略的整体效果统计。
        """
        events = [event.payload for event in self.events if event.name in {'retrieval', 'retrieval_multi'}]
        if last_n is not None and last_n > 0:
            events = events[-last_n:]
        if not events:
            return {
                'requests': 0,
                'question_oriented_requests': 0,
                'parent_chunk_requests': 0,
                'multi_vector_hits': 0,
                'aggregated_targets': 0,
                'aggregated_away_candidates': 0,
                'parent_expanded': 0,
                'parent_document_hits': 0,
                'matched_via_breakdown': {},
            }

        question_oriented_requests = 0
        parent_chunk_requests = 0
        multi_vector_hits = 0
        aggregated_targets = 0
        aggregated_away_candidates = 0
        parent_expanded = 0
        parent_document_hits = 0
        matched_via_breakdown: dict[str, int] = {}

        for item in events:
            if item.get('use_question_oriented_index'):
                question_oriented_requests += 1
            if item.get('use_parent_chunk_retrieval'):
                parent_chunk_requests += 1

            pre_rerank = item.get('pre_rerank') or []
            post_aggregate = item.get('post_aggregate') or []
            aggregated_targets += len(post_aggregate)
            aggregated_away_candidates += max(len(pre_rerank) - len(post_aggregate), 0)
            for hit in post_aggregate:
                matched_via = [str(marker) for marker in hit.get('matched_via') or [] if str(marker).strip()]
                if len(matched_via) >= 2:
                    multi_vector_hits += 1
                for marker in matched_via:
                    matched_via_breakdown[marker] = matched_via_breakdown.get(marker, 0) + 1

            parent_info = item.get('parent_chunk') or {}
            parent_expanded += int(parent_info.get('expanded', 0))
            parent_document_hits += int(parent_info.get('parent_document_hits', 0))

        return {
            'requests': len(events),
            'question_oriented_requests': question_oriented_requests,
            'parent_chunk_requests': parent_chunk_requests,
            'multi_vector_hits': multi_vector_hits,
            'aggregated_targets': aggregated_targets,
            'aggregated_away_candidates': aggregated_away_candidates,
            'parent_expanded': parent_expanded,
            'parent_document_hits': parent_document_hits,
            'matched_via_breakdown': matched_via_breakdown,
        }

    def summarize_graph_retrieval(self, last_n: int | None = None) -> dict:
        """聚合 GraphRAG 的种子命中、扩边和证据回填情况。

        Args:
            last_n: 仅统计最近 N 条相关事件；为空时统计全部。

        Returns:
            GraphRAG 相关召回效果的聚合指标。
        """
        events = [event.payload for event in self.events if event.name in {'retrieval', 'retrieval_multi'}]
        if last_n is not None and last_n > 0:
            events = events[-last_n:]
        if not events:
            return {
                'requests': 0,
                'graph_requests': 0,
                'graph_candidates': 0,
                'graph_seed_node_count': 0,
                'graph_expanded_edge_count': 0,
                'graph_returned_citations': 0,
                'avg_graph_max_hops': 0.0,
            }

        graph_requests = 0
        graph_candidates = 0
        graph_seed_node_count = 0
        graph_expanded_edge_count = 0
        graph_returned_citations = 0
        max_hops_values: list[int] = []
        for item in events:
            if not item.get('use_graph_rag'):
                continue
            graph_requests += 1
            graph_candidates += int(item.get('graph_candidates', 0) or 0)
            graph_info = item.get('graph') or {}
            graph_seed_node_count += int(graph_info.get('seed_node_count', 0) or 0)
            graph_expanded_edge_count += int(graph_info.get('expanded_edge_count', 0) or 0)
            graph_returned_citations += int(graph_info.get('returned_citations', 0) or 0)
            max_hops_values.append(int(item.get('graph_max_hops', 0) or 0))

        return {
            'requests': len(events),
            'graph_requests': graph_requests,
            'graph_candidates': graph_candidates,
            'graph_seed_node_count': graph_seed_node_count,
            'graph_expanded_edge_count': graph_expanded_edge_count,
            'graph_returned_citations': graph_returned_citations,
            'avg_graph_max_hops': round(sum(max_hops_values) / len(max_hops_values), 4) if max_hops_values else 0.0,
        }

    def summarize_model_routes(self, last_n: int | None = None) -> dict:
        """聚合模型路由选择与实际消费情况。"""
        selected_events = [event.payload for event in self.events if event.name == 'model_route_selected']
        consumed_events = [event.payload for event in self.events if event.name == 'model_route_consumed']
        if last_n is not None and last_n > 0:
            selected_events = selected_events[-last_n:]
            consumed_events = consumed_events[-last_n:]
        if not selected_events and not consumed_events:
            return {
                'selected': 0,
                'consumed': 0,
                'llm_selected': 0,
                'fallback_selected': 0,
                'avg_estimated_cost_units': 0.0,
                'avg_actual_cost_units': 0.0,
                'total_estimated_cost_units': 0.0,
                'total_actual_cost_units': 0.0,
                'avg_prompt_tokens': 0.0,
                'avg_completion_tokens': 0.0,
                'avg_total_tokens': 0.0,
                'provider_reported_count': 0,
                'provider_cost_count': 0,
                'provider_usage_count': 0,
                'local_estimate_count': 0,
                'scope_breakdown': {},
                'purpose_breakdown': {},
            }

        scope_breakdown: dict[str, int] = {}
        purpose_breakdown: dict[str, int] = {}
        for item in selected_events:
            scope = str(item.get('scope') or 'unknown')
            purpose = str(item.get('purpose') or item.get('scope') or 'unknown')
            scope_breakdown[scope] = scope_breakdown.get(scope, 0) + 1
            purpose_breakdown[purpose] = purpose_breakdown.get(purpose, 0) + 1

        estimated_costs = [float(item.get('estimated_cost_units', 0.0) or 0.0) for item in selected_events]
        actual_costs = [float(item.get('actual_cost_units', 0.0) or 0.0) for item in consumed_events]
        prompt_tokens = [int(item.get('prompt_tokens', 0) or 0) for item in consumed_events]
        completion_tokens = [int(item.get('completion_tokens', 0) or 0) for item in consumed_events]
        total_tokens = [int(item.get('total_tokens', 0) or 0) for item in consumed_events]
        provider_cost_count = sum(1 for item in consumed_events if str(item.get('cost_source') or '') == 'provider_cost')
        provider_usage_count = sum(1 for item in consumed_events if str(item.get('cost_source') or '') == 'provider_usage')
        local_estimate_count = sum(1 for item in consumed_events if str(item.get('cost_source') or '') == 'local_estimate')
        provider_reported_count = sum(1 for item in consumed_events if bool(item.get('provider_reported')))

        return {
            'selected': len(selected_events),
            'consumed': len(consumed_events),
            'llm_selected': sum(1 for item in selected_events if str(item.get('mode') or '') == 'llm'),
            'fallback_selected': sum(1 for item in selected_events if str(item.get('mode') or '') != 'llm'),
            'avg_estimated_cost_units': round(sum(estimated_costs) / len(estimated_costs), 4) if estimated_costs else 0.0,
            'avg_actual_cost_units': round(sum(actual_costs) / len(actual_costs), 4) if actual_costs else 0.0,
            'total_estimated_cost_units': round(sum(estimated_costs), 4),
            'total_actual_cost_units': round(sum(actual_costs), 4),
            'avg_prompt_tokens': round(sum(prompt_tokens) / len(prompt_tokens), 4) if prompt_tokens else 0.0,
            'avg_completion_tokens': round(sum(completion_tokens) / len(completion_tokens), 4) if completion_tokens else 0.0,
            'avg_total_tokens': round(sum(total_tokens) / len(total_tokens), 4) if total_tokens else 0.0,
            'provider_reported_count': provider_reported_count,
            'provider_cost_count': provider_cost_count,
            'provider_usage_count': provider_usage_count,
            'local_estimate_count': local_estimate_count,
            'scope_breakdown': dict(sorted(scope_breakdown.items())),
            'purpose_breakdown': dict(sorted(purpose_breakdown.items())),
        }

    def summarize_task_workflows(self, last_n: int | None = None) -> dict:
        """聚合任务工作流的执行质量。

        Args:
            last_n: 仅统计最近 N 个已完成任务相关事件；为空时统计全部。

        Returns:
            任务执行成功率、时延、工具调用和子 Agent 效果等综合指标。
        """

        started_events = [event.payload for event in self.events if event.name == 'task_started']
        completed_events = [event.payload for event in self.events if event.name == 'task_completed']
        tool_events = [event.payload for event in self.events if event.name == 'agent_tool_call']
        step_events = [event.payload for event in self.events if event.name == 'task_step_completed']
        finalized_events = [event.payload for event in self.events if event.name == 'task_workflow_finalized']
        review_events = [event.payload for event in self.events if event.name == 'task_review_completed']
        replan_events = [event.payload for event in self.events if event.name == 'task_replanned']
        artifact_events = [event.payload for event in self.events if event.name == 'task_artifact_stored']
        sub_agent_started_events = [event.payload for event in self.events if event.name == 'task_sub_agent_started']
        sub_agent_completed_events = [event.payload for event in self.events if event.name == 'task_sub_agent_completed']
        sub_agent_failed_events = [event.payload for event in self.events if event.name == 'task_sub_agent_failed']
        retrieval_events = [
            event.payload
            for event in self.events
            if event.name in {'retrieval', 'retrieval_multi'} and str(event.payload.get('task_id') or '').strip()
        ]
        if last_n is not None and last_n > 0:
            completed_events = completed_events[-last_n:]
            relevant_task_ids = {str(item.get('task_id') or '') for item in completed_events}
            started_events = [item for item in started_events if str(item.get('task_id') or '') in relevant_task_ids]
            tool_events = [item for item in tool_events if str(item.get('task_id') or '') in relevant_task_ids]
            step_events = [item for item in step_events if str(item.get('task_id') or '') in relevant_task_ids]
            finalized_events = [item for item in finalized_events if str(item.get('task_id') or '') in relevant_task_ids]
            review_events = [item for item in review_events if str(item.get('task_id') or '') in relevant_task_ids]
            replan_events = [item for item in replan_events if str(item.get('task_id') or '') in relevant_task_ids]
            artifact_events = [item for item in artifact_events if str(item.get('task_id') or '') in relevant_task_ids]
            sub_agent_started_events = [
                item for item in sub_agent_started_events if str(item.get('task_id') or '') in relevant_task_ids
            ]
            sub_agent_completed_events = [
                item for item in sub_agent_completed_events if str(item.get('task_id') or '') in relevant_task_ids
            ]
            sub_agent_failed_events = [
                item for item in sub_agent_failed_events if str(item.get('task_id') or '') in relevant_task_ids
            ]
            retrieval_events = [item for item in retrieval_events if str(item.get('task_id') or '') in relevant_task_ids]

        completed = [item for item in completed_events if item.get('status') == 'completed']
        failed = [item for item in completed_events if item.get('status') == 'failed']
        latency_values = [
            int((item.get('metrics') or {}).get('latency_ms', 0))
            for item in completed
            if isinstance(item.get('metrics'), dict)
        ]
        tool_error_count = sum(1 for item in tool_events if item.get('status') == 'error')
        total_tool_calls = len(tool_events)
        avg_latency_ms = round(sum(latency_values) / len(latency_values), 2) if latency_values else 0.0
        p95_latency_ms = _percentile(latency_values, 0.95)
        completed_task_ids = {str(item.get('task_id') or '') for item in completed}
        step_count_by_task: dict[str, int] = {}
        step_breakdown: dict[str, int] = {}
        for item in step_events:
            task_id = str(item.get('task_id') or '')
            if not task_id:
                continue
            step_count_by_task[task_id] = step_count_by_task.get(task_id, 0) + 1
            step_name = str(item.get('step') or 'unknown')
            step_breakdown[step_name] = step_breakdown.get(step_name, 0) + 1
        finalized_by_task: dict[str, dict] = {
            str(item.get('task_id') or ''): item for item in finalized_events if str(item.get('task_id') or '').strip()
        }
        review_failed = [item for item in review_events if not item.get('passed')]
        unsupported_claim_total = sum(int(item.get('unsupported_claim_count', 0) or 0) for item in review_events)
        artifact_versions_by_task: dict[str, int] = {}
        final_artifact_count = 0
        for item in artifact_events:
            task_id = str(item.get('task_id') or '')
            if not task_id:
                continue
            artifact_versions_by_task[task_id] = artifact_versions_by_task.get(task_id, 0) + 1
            if str(item.get('status') or '') == 'final':
                final_artifact_count += 1
        review_fix_rate = (
            round(
                sum(
                    1
                    for item in finalized_events
                    if str(item.get('task_id') or '') in {str(event.get('task_id') or '') for event in review_failed}
                    and bool(item.get('final_review_passed'))
                )
                / len(review_failed),
                4,
            )
            if review_failed
            else 0.0
        )
        avg_steps_per_task = round(
            sum(step_count_by_task.get(task_id, 0) for task_id in completed_task_ids) / len(completed_task_ids),
            2,
        ) if completed_task_ids else 0.0
        avg_plan_version = round(
            sum(int(item.get('plan_version', 1) or 1) for item in finalized_events) / len(finalized_events),
            2,
        ) if finalized_events else 0.0
        avg_artifact_versions = round(
            sum(artifact_versions_by_task.get(task_id, 0) for task_id in completed_task_ids) / len(completed_task_ids),
            2,
        ) if completed_task_ids else 0.0
        avg_artifact_memory_count = round(
            sum(int(item.get('artifact_memory_count', 0) or 0) for item in finalized_events) / len(finalized_events),
            2,
        ) if finalized_events else 0.0
        avg_task_memory_count = round(
            sum(int(item.get('task_memory_count', 0) or 0) for item in finalized_events) / len(finalized_events),
            2,
        ) if finalized_events else 0.0
        evidence_gap_replans = sum(1 for item in replan_events if str(item.get('trigger') or '') == 'evidence_gap')
        review_replans = sum(1 for item in replan_events if str(item.get('trigger') or '') == 'review_failed')
        retrieval_mode_breakdown: dict[str, int] = {}
        rerank_mode_breakdown: dict[str, int] = {}
        retrieval_candidate_total = 0
        retrieval_selected_total = 0
        for item in retrieval_events:
            retrieval_mode = str(item.get('retrieval_mode') or '').strip()
            if retrieval_mode:
                retrieval_mode_breakdown[retrieval_mode] = retrieval_mode_breakdown.get(retrieval_mode, 0) + 1
            rerank_mode = str(item.get('rerank_mode') or '').strip()
            if rerank_mode:
                rerank_mode_breakdown[rerank_mode] = rerank_mode_breakdown.get(rerank_mode, 0) + 1
            retrieval_candidate_total += sum(
                int(item.get(key, 0) or 0) for key in ('dense_candidates', 'lexical_candidates', 'graph_candidates')
            )
            retrieval_selected_total += int(item.get('hits', 0) or 0)
        tool_breakdown: dict[str, dict[str, float]] = {}
        for item in tool_events:
            tool_name = str(item.get('tool_name') or 'unknown')
            tool_entry = tool_breakdown.setdefault(
                tool_name,
                {'call_count': 0.0, 'error_count': 0.0, 'total_duration_ms': 0.0},
            )
            tool_entry['call_count'] += 1.0
            tool_entry['error_count'] += 1.0 if item.get('status') == 'error' else 0.0
            tool_entry['total_duration_ms'] += float(item.get('duration_ms', 0) or 0.0)
        formatted_tool_breakdown: dict[str, dict[str, float]] = {}
        for tool_name, values in tool_breakdown.items():
            call_count = values['call_count']
            formatted_tool_breakdown[tool_name] = {
                'call_count': round(call_count, 4),
                'error_rate': round(values['error_count'] / call_count, 4) if call_count else 0.0,
                'avg_duration_ms': round(values['total_duration_ms'] / call_count, 4) if call_count else 0.0,
            }
        avg_tool_error_count = round(
            sum(item['error_count'] for item in tool_breakdown.values()) / len(completed_task_ids),
            4,
        ) if completed_task_ids else 0.0
        route_cost_by_task: dict[str, float] = {}
        for item in [event.payload for event in self.events if event.name == 'model_route_consumed']:
            task_id = str(item.get('task_id') or '').strip()
            if not task_id:
                continue
            if completed_task_ids and task_id not in completed_task_ids:
                continue
            route_cost_by_task[task_id] = route_cost_by_task.get(task_id, 0.0) + float(item.get('actual_cost_units', 0.0) or 0.0)
        heuristic_avg_cost_per_task = round(
            sum(
                (
                    float(item.get('tool_calls', 0) or 0)
                    + float(item.get('artifact_count', 0) or 0) * 0.3
                    + float(item.get('plan_revision_count', 0) or 0) * 0.5
                )
                for item in finalized_events
            ) / len(finalized_events),
            4,
        ) if finalized_events else 0.0
        actual_avg_cost_per_task = round(
            sum(route_cost_by_task.get(task_id, 0.0) for task_id in completed_task_ids) / len(completed_task_ids),
            4,
        ) if completed_task_ids and route_cost_by_task else 0.0
        avg_cost_per_task = actual_avg_cost_per_task or heuristic_avg_cost_per_task

        class _SubAgentBreakdownEntry(TypedDict):
            """聚合单个子代理在最近窗口中的执行统计。"""

            run_count: int
            failure_count: int
            selected_tools: dict[str, int]
            actions: dict[str, int]

        sub_agent_breakdown: dict[str, _SubAgentBreakdownEntry] = {}
        action_breakdown: dict[str, int] = {}
        selected_tool_breakdown: dict[str, int] = {}
        for item in sub_agent_completed_events:
            agent_name = str(item.get('agent_name') or 'unknown')
            action_name = str(item.get('action') or 'unknown')
            key = f'{agent_name}::{action_name}'
            action_breakdown[key] = action_breakdown.get(key, 0) + 1
            sub_agent_entry = cast(
                _SubAgentBreakdownEntry,
                sub_agent_breakdown.setdefault(
                    agent_name,
                    {'run_count': 0, 'failure_count': 0, 'selected_tools': {}, 'actions': {}},
                ),
            )
            sub_agent_entry['run_count'] += 1
            sub_agent_entry['actions'][action_name] = sub_agent_entry['actions'].get(action_name, 0) + 1
            for tool_name in item.get('selected_tools') or []:
                tool_name = str(tool_name or 'unknown')
                selected_tool_breakdown[tool_name] = selected_tool_breakdown.get(tool_name, 0) + 1
                sub_agent_entry['selected_tools'][tool_name] = sub_agent_entry['selected_tools'].get(tool_name, 0) + 1
        for item in sub_agent_failed_events:
            agent_name = str(item.get('agent_name') or 'unknown')
            sub_agent_entry = cast(
                _SubAgentBreakdownEntry,
                sub_agent_breakdown.setdefault(
                    agent_name,
                    {'run_count': 0, 'failure_count': 0, 'selected_tools': {}, 'actions': {}},
                ),
            )
            sub_agent_entry['failure_count'] += 1
        formatted_sub_agent_breakdown: dict[str, dict[str, Any]] = {}
        for agent_name, agent_values in sub_agent_breakdown.items():
            run_count = int(agent_values['run_count'])
            failure_count = int(agent_values['failure_count'])
            formatted_sub_agent_breakdown[agent_name] = {
                'run_count': run_count,
                'failure_count': failure_count,
                'failure_rate': round(failure_count / run_count, 4) if run_count else 0.0,
                'selected_tools': dict(sorted(agent_values['selected_tools'].items())),
                'actions': dict(sorted(agent_values['actions'].items())),
            }
        return {
            'started': len(started_events),
            'completed': len(completed),
            'failed': len(failed),
            'success_rate': round(len(completed) / len(completed_events), 4) if completed_events else 0.0,
            'avg_latency_ms': avg_latency_ms,
            'p95_task_latency_ms': p95_latency_ms,
            'tool_calls': total_tool_calls,
            'tool_error_rate': round(tool_error_count / total_tool_calls, 4) if total_tool_calls else 0.0,
            'step_events': len(step_events),
            'avg_steps_per_task': avg_steps_per_task,
            'step_failure_rate': round(len(failed) / len(step_events), 4) if step_events else 0.0,
            'review_count': len(review_events),
            'review_failed': len(review_failed),
            'review_fix_rate': review_fix_rate,
            'unsupported_claim_rate': round(unsupported_claim_total / len(review_events), 4) if review_events else 0.0,
            'replan_count': len(replan_events),
            'evidence_gap_replans': evidence_gap_replans,
            'review_replans': review_replans,
            'avg_plan_version': avg_plan_version,
            'artifact_events': len(artifact_events),
            'final_artifact_count': final_artifact_count,
            'avg_artifact_versions': avg_artifact_versions,
            'avg_artifact_memory_count': avg_artifact_memory_count,
            'avg_task_memory_count': avg_task_memory_count,
            'avg_tool_error_count': avg_tool_error_count,
            'step_breakdown': step_breakdown,
            'tool_breakdown': formatted_tool_breakdown,
            'avg_cost_per_task': avg_cost_per_task,
            'sub_agent_started': len(sub_agent_started_events),
            'sub_agent_completed': len(sub_agent_completed_events),
            'sub_agent_failed': len(sub_agent_failed_events),
            'sub_agent_failure_rate': (
                round(len(sub_agent_failed_events) / len(sub_agent_completed_events), 4)
                if sub_agent_completed_events
                else 0.0
            ),
            'avg_sub_agent_runs_per_task': (
                round(len(sub_agent_completed_events) / len(completed_task_ids), 4) if completed_task_ids else 0.0
            ),
            'sub_agent_breakdown': formatted_sub_agent_breakdown,
            'sub_agent_action_breakdown': dict(sorted(action_breakdown.items())),
            'sub_agent_selected_tool_breakdown': dict(sorted(selected_tool_breakdown.items())),
            'retrieval_events': len(retrieval_events),
            'avg_retrieval_candidate_count': (
                round(retrieval_candidate_total / len(retrieval_events), 4) if retrieval_events else 0.0
            ),
            'avg_retrieval_selected_count': (
                round(retrieval_selected_total / len(retrieval_events), 4) if retrieval_events else 0.0
            ),
            'retrieval_mode_breakdown': retrieval_mode_breakdown,
            'rerank_mode_breakdown': rerank_mode_breakdown,
        }


def _percentile(values: list[int], percentile: float) -> float:
    """计算整数列表的近似分位数。

    Args:
        values: 待统计数值列表。
        percentile: 分位值，通常在 0 到 1 之间。

    Returns:
        对应分位位置的浮点数值；输入为空时返回 `0.0`。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return float(ordered[index])

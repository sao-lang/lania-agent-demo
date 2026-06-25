"""健康与指标端点测试，覆盖上下文压缩、缓存、检索增强以及追踪摘要暴露。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


class HealthMetricsTests(unittest.TestCase):
    """健康与指标测试集合，关注运行时状态、缓存统计与追踪指标的暴露。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.app = create_app()
        self.client = TestClient(self.app)
        self.container = self.app.state.container
        self.container.settings = Settings(
            DATA_DIR=Path(tempfile.mkdtemp()),
            ENABLE_CONTEXT_COMPRESSION=True,
            CONTEXT_COMPRESSION_MAX_CHUNKS=3,
            CONTEXT_COMPRESSION_MAX_SENTENCES=6,
            CONTEXT_COMPRESSION_MAX_CHARS=900,
            CONVERTED_CACHE_MAX_FILES=2,
            CONVERTED_CACHE_TTL_SECONDS=3600,
            KNOWLEDGE_CAPABILITY_PROVIDER='remote_http',
            KNOWLEDGE_CAPABILITY_BASE_URL='http://knowledge.test',
            SANDBOX_EXECUTOR_PROVIDER='remote_http',
            SANDBOX_EXECUTOR_BASE_URL='http://sandbox.test',
        )
        self.container.ingestion.settings = self.container.settings
        self.container.vector_store.ping = lambda: True
        self.container.eval_service.get_runtime_status = lambda: {'ready': True}
        self.container.trace.record(
            'context_compressed',
            {
                'enabled': True,
                'original_chunk_count': 4,
                'compressed_chunk_count': 2,
                'original_sentence_count': 10,
                'compressed_sentence_count': 4,
                'original_char_count': 1000,
                'compressed_char_count': 420,
                'strategy': 'sentence_extract',
            },
        )
        self.container.trace.record(
            'semantic_chunking_prepared',
            {
                'collection_name': 'demo',
                'requested_strategy': 'semantic',
                'source_segments': 5,
                'prepared_segments': 3,
                'semantic_segments': 2,
                'fixed_segments': 1,
                'prepared_groups': 1,
                'merged_source_segments': 2,
            },
        )
        cache_dir = self.container.ingestion._converted_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        old_cache = cache_dir / 'old.docx'
        keep_cache = cache_dir / 'keep.docx'
        extra_cache = cache_dir / 'extra.docx'
        old_cache.write_bytes(b'12345')
        keep_cache.write_bytes(b'67890')
        extra_cache.write_bytes(b'abcde')
        now_ts = time.time()
        stale_ts = now_ts - 7200
        recent_ts = now_ts - 10
        os.utime(old_cache, (stale_ts, stale_ts))
        os.utime(keep_cache, (recent_ts - 1, recent_ts - 1))
        os.utime(extra_cache, (recent_ts, recent_ts))
        self.container.ingestion._prune_converted_cache(cache_dir)
        now = datetime.now(timezone.utc)
        self.container.state.semantic_cache['sc-1'] = {
            'cache_id': 'sc-1',
            'collection_name': 'demo',
            'mode': 'query',
            'question': '会话摘要是什么',
            'normalized_question': '会话摘要是什么',
            'question_embedding': [1.0, 0.0],
            'context_signature': None,
            'filters': None,
            'filters_signature': 'filters',
            'strategy_signature': 'strategy',
            'answer': '会话摘要用于压缩历史消息。',
            'answer_mode': 'local_fallback',
            'citations': [],
            'source_doc_ids': ['doc-1'],
            'metadata': {},
            'hit_count': 2,
            'created_at': now,
            'updated_at': now,
            'last_hit_at': now,
        }
        self.container.trace.record(
            'semantic_cache_lookup',
            {
                'enabled': True,
                'collection_name': 'demo',
                'mode': 'query',
                'hit': True,
                'match_type': 'semantic',
                'similarity': 0.97,
                'candidate_count': 1,
                'reason': 'semantic_match',
            },
        )
        self.container.trace.record(
            'semantic_cache_lookup',
            {
                'enabled': True,
                'collection_name': 'demo',
                'mode': 'query',
                'hit': False,
                'match_type': None,
                'similarity': 0.41,
                'candidate_count': 1,
                'reason': 'similarity_below_threshold',
            },
        )
        self.container.trace.record(
            'context_compressed',
            {
                'enabled': True,
                'original_chunk_count': 6,
                'compressed_chunk_count': 2,
                'original_sentence_count': 16,
                'compressed_sentence_count': 5,
                'original_char_count': 1200,
                'compressed_char_count': 300,
                'strategy': 'sentence_extract',
            },
        )
        self.container.trace.record(
            'context_compressed',
            {
                'enabled': True,
                'original_chunk_count': 5,
                'compressed_chunk_count': 3,
                'original_sentence_count': 12,
                'compressed_sentence_count': 6,
                'original_char_count': 800,
                'compressed_char_count': 360,
                'strategy': 'sentence_extract',
            },
        )
        self.container.trace.record(
            'retrieval',
            {
                'collection_name': 'demo',
                'top_k': 3,
                'hits': 2,
                'filters': {},
                'effective_filters': {'index_kind': ['content', 'query_hint', 'title_summary']},
                'query': 'session summary 怎么看',
                'use_hybrid_retrieval': True,
                'retrieval_mode': 'hybrid',
                'dense_candidates': 3,
                'lexical_candidates': 2,
                'use_rerank': False,
                'rerank_mode': 'disabled',
                'use_long_context_reorder': False,
                'use_parent_chunk_retrieval': True,
                'use_question_oriented_index': True,
                'use_graph_rag': True,
                'graph_max_hops': 2,
                'graph_candidates': 2,
                'graph': {
                    'seed_node_count': 2,
                    'expanded_edge_count': 3,
                    'returned_citations': 2,
                },
                'parent_chunk': {
                    'expanded': 2,
                    'deduplicated': 1,
                    'parent_document_hits': 2,
                    'source': 'parent_documents',
                },
                'dense_ranked': [],
                'lexical_ranked': [],
                'pre_rerank': [
                    {'chunk_id': 'shared', 'matched_via': ['content']},
                    {'chunk_id': 'shared', 'matched_via': ['query_hint']},
                    {'chunk_id': 'shared', 'matched_via': ['title_summary']},
                ],
                'post_aggregate': [
                    {'chunk_id': 'shared', 'matched_via': ['content', 'query_hint', 'title_summary']},
                    {'chunk_id': 'dense-1', 'matched_via': ['content']},
                ],
                'post_rerank': [],
                'post_reorder': [],
            },
        )
        self.container.state.graph_nodes['gn-1'] = {
            'node_id': 'gn-1',
            'collection_name': 'demo',
            'name': 'session summary',
            'normalized_name': 'session summary',
            'entity_type': 'concept',
            'aliases': ['summary'],
            'doc_ids': ['doc-1'],
            'mention_count': 2,
            'metadata': {},
            'created_at': now,
            'updated_at': now,
        }
        self.container.state.graph_edges['ge-1'] = {
            'edge_id': 'ge-1',
            'collection_name': 'demo',
            'doc_id': 'doc-1',
            'source_node_id': 'gn-1',
            'source_name': 'session summary',
            'target_node_id': 'gn-2',
            'target_name': 'chat session',
            'relation': 'related_to',
            'normalized_relation': 'related_to',
            'evidence_chunk_id': 'c1',
            'evidence_text': 'session summary 与 chat session 相关。',
            'weight': 0.2,
            'metadata': {},
            'created_at': now,
            'updated_at': now,
        }
        self.container.trace.record('task_started', {'task_id': 'task-1', 'task_type': 'document_analysis'})
        for step in ['load_task', 'plan_task', 'retrieve_evidence', 'analyze', 'draft_artifact', 'review_artifact', 'finalize']:
            self.container.trace.record('task_step_completed', {'task_id': 'task-1', 'step': step})
        self.container.trace.record(
            'agent_tool_call',
            {'task_id': 'task-1', 'tool_name': 'retrieve_evidence', 'duration_ms': 12, 'status': 'ok'},
        )
        self.container.trace.record(
            'agent_tool_call',
            {'task_id': 'task-1', 'tool_name': 'review_report', 'duration_ms': 8, 'status': 'error'},
        )
        self.container.trace.record(
            'task_review_completed',
            {'task_id': 'task-1', 'passed': False, 'unsupported_claim_count': 1, 'missing_section_count': 1},
        )
        self.container.trace.record(
            'task_sub_agent_started',
            {'task_id': 'task-1', 'agent_name': 'evidence_agent', 'action': 'collect'},
        )
        self.container.trace.record(
            'task_sub_agent_completed',
            {
                'task_id': 'task-1',
                'agent_name': 'evidence_agent',
                'action': 'collect_evidence',
                'selected_tools': ['retrieve_evidence', 'retrieve_graph_evidence'],
            },
        )
        self.container.trace.record(
            'task_sub_agent_started',
            {'task_id': 'task-1', 'agent_name': 'review_agent', 'action': 'review'},
        )
        self.container.trace.record(
            'task_sub_agent_completed',
            {
                'task_id': 'task-1',
                'agent_name': 'review_agent',
                'action': 'review_artifact',
                'selected_tools': ['review_report'],
            },
        )
        self.container.trace.record(
            'retrieval',
            {
                'task_id': 'task-1',
                'step_name': 'retrieve_evidence',
                'tool_name': 'retrieve_evidence',
                'retrieval_mode': 'hybrid_graph',
                'rerank_mode': 'lexical',
                'dense_candidates': 4,
                'lexical_candidates': 2,
                'graph_candidates': 1,
                'hits': 3,
            },
        )
        self.container.trace.record(
            'task_replanned',
            {'task_id': 'task-1', 'trigger': 'review_failed', 'plan_version': 2},
        )
        self.container.trace.record(
            'task_artifact_stored',
            {'task_id': 'task-1', 'artifact_id': 'artifact-1', 'artifact_type': 'document_analysis_report', 'version': 1, 'status': 'draft'},
        )
        self.container.trace.record(
            'task_artifact_stored',
            {'task_id': 'task-1', 'artifact_id': 'artifact-2', 'artifact_type': 'document_analysis_report', 'version': 2, 'status': 'final'},
        )
        self.container.trace.record(
            'task_workflow_finalized',
            {
                'task_id': 'task-1',
                'step_count': 7,
                'tool_calls': 2,
                'latency_ms': 120,
                'sub_agent_runs': 2,
                'sub_agent_failures': 0,
                'plan_version': 2,
                'artifact_count': 2,
                'task_memory_count': 5,
                'artifact_memory_count': 2,
                'plan_revision_count': 1,
                'final_review_passed': True,
                'unsupported_claim_count': 0,
            },
        )
        self.container.trace.record(
            'task_completed',
            {'task_id': 'task-1', 'status': 'completed', 'metrics': {'latency_ms': 120}},
        )
        self.container.trace.record(
            'model_route_selected',
            {
                'scope': 'task_tool',
                'task_id': 'task-1',
                'tool_name': 'review_report',
                'step_name': 'review_artifact',
                'purpose': 'task_review',
                'mode': 'llm',
                'profile': 'quality',
                'reason': 'route_selected',
                'estimated_cost_units': 2.3,
            },
        )
        self.container.trace.record(
            'model_route_consumed',
            {
                'scope': 'task_tool',
                'task_id': 'task-1',
                'tool_name': 'review_report',
                'step_name': 'review_artifact',
                'purpose': 'task_review',
                'mode': 'llm',
                'profile': 'quality',
                'reason': 'route_selected',
                'estimated_cost_units': 2.3,
                'prompt_tokens': 80,
                'completion_tokens': 40,
                'total_tokens': 120,
                'actual_cost_units': 0.276,
                'cost_source': 'provider_usage',
                'provider_reported': True,
            },
        )

    def test_health_exposes_context_compression_runtime_summary(self) -> None:
        """覆盖 `health_exposes_context_compression_runtime_summary` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with patch(
            'app.api.v1.endpoints.health._probe_remote_worker_sync',
            side_effect=[
                {
                    'probe_enabled': True,
                    'probed': True,
                    'probe_ok': True,
                    'probe_status_code': 200,
                    'probe_latency_ms': 12,
                    'probe_error': None,
                    'probe_response_status': 'ok',
                    'probe_ready': True,
                },
                {
                    'probe_enabled': True,
                    'probed': True,
                    'probe_ok': True,
                    'probe_status_code': 200,
                    'probe_latency_ms': 9,
                    'probe_error': None,
                    'probe_response_status': 'ok',
                    'probe_ready': True,
                },
            ],
        ):
            response = self.client.get('/api/v1/health')

        self.assertEqual(response.status_code, 200)
        payload = response.json()['model_runtime']['context_compression']
        self.assertTrue(payload['enabled_by_default'])
        self.assertEqual(payload['max_chunks'], 3)
        self.assertEqual(payload['max_sentences'], 6)
        self.assertEqual(payload['max_chars'], 900)
        self.assertEqual(payload['compressed_requests'], 3)
        self.assertEqual(payload['total_original_char_count'], 3000)
        self.assertEqual(payload['total_compressed_char_count'], 1080)
        self.assertAlmostEqual(payload['avg_char_reduction_ratio'], 0.64, places=4)
        self.assertEqual(payload['strategy_breakdown']['sentence_extract'], 3)
        self.assertEqual(payload['recent_window']['size'], 20)
        self.assertEqual(payload['recent_window']['compressed_requests'], 3)
        semantic_chunking = response.json()['model_runtime']['semantic_chunking']
        self.assertEqual(semantic_chunking['default_strategy'], 'fixed')
        self.assertEqual(semantic_chunking['documents'], 1)
        self.assertEqual(semantic_chunking['requested_semantic_documents'], 1)
        self.assertEqual(semantic_chunking['source_segments'], 5)
        self.assertEqual(semantic_chunking['prepared_segments'], 3)
        self.assertEqual(semantic_chunking['semantic_segments'], 2)
        self.assertEqual(semantic_chunking['fixed_segments'], 1)
        self.assertEqual(semantic_chunking['prepared_groups'], 1)
        self.assertAlmostEqual(semantic_chunking['avg_merge_ratio'], 0.4, places=4)
        retrieval_enhancements = response.json()['model_runtime']['retrieval_enhancements']
        self.assertEqual(retrieval_enhancements['requests'], 2)
        self.assertEqual(retrieval_enhancements['question_oriented_requests'], 1)
        self.assertEqual(retrieval_enhancements['parent_chunk_requests'], 1)
        self.assertEqual(retrieval_enhancements['multi_vector_hits'], 1)
        self.assertEqual(retrieval_enhancements['aggregated_targets'], 2)
        self.assertEqual(retrieval_enhancements['aggregated_away_candidates'], 1)
        self.assertEqual(retrieval_enhancements['parent_expanded'], 2)
        self.assertEqual(retrieval_enhancements['parent_document_hits'], 2)
        self.assertEqual(retrieval_enhancements['matched_via_breakdown']['content'], 2)
        self.assertEqual(retrieval_enhancements['matched_via_breakdown']['query_hint'], 1)
        self.assertEqual(retrieval_enhancements['matched_via_breakdown']['title_summary'], 1)
        graph_rag = response.json()['model_runtime']['graph_rag']
        self.assertEqual(graph_rag['node_count'], 1)
        self.assertEqual(graph_rag['edge_count'], 1)
        self.assertEqual(graph_rag['graph_requests'], 1)
        self.assertEqual(graph_rag['graph_candidates'], 2)
        self.assertEqual(graph_rag['graph_seed_node_count'], 2)
        self.assertEqual(graph_rag['graph_expanded_edge_count'], 3)
        self.assertEqual(graph_rag['graph_returned_citations'], 2)
        self.assertAlmostEqual(graph_rag['avg_graph_max_hops'], 2.0, places=4)
        model_routing = response.json()['model_runtime']['model_routing']
        self.assertEqual(model_routing['selected'], 1)
        self.assertEqual(model_routing['consumed'], 1)
        self.assertAlmostEqual(model_routing['avg_estimated_cost_units'], 2.3, places=4)
        self.assertAlmostEqual(model_routing['avg_actual_cost_units'], 0.276, places=4)
        self.assertEqual(model_routing['provider_usage_count'], 1)
        self.assertEqual(model_routing['avg_total_tokens'], 120.0)
        semantic_cache = response.json()['model_runtime']['semantic_cache']
        self.assertTrue(semantic_cache['enabled_by_default'])
        self.assertGreaterEqual(semantic_cache['entry_count'], 1)
        self.assertEqual(semantic_cache['lifetime_hits'], 2)
        self.assertEqual(semantic_cache['hits'], 1)
        self.assertEqual(semantic_cache['misses'], 1)
        self.assertAlmostEqual(semantic_cache['hit_rate'], 0.5, places=4)
        office_cache = response.json()['model_runtime']['office_conversion_cache']
        self.assertEqual(office_cache['file_count'], 2)
        self.assertEqual(office_cache['total_bytes'], 10)
        self.assertEqual(office_cache['max_files'], 2)
        self.assertEqual(office_cache['ttl_seconds'], 3600)
        self.assertEqual(office_cache['prune_runs'], 1)
        self.assertEqual(office_cache['deleted_files'], 1)
        self.assertIsNotNone(office_cache['last_pruned_at'])
        task_workflows = response.json()['model_runtime']['task_workflows']
        self.assertEqual(task_workflows['started'], 1)
        self.assertEqual(task_workflows['completed'], 1)
        self.assertEqual(task_workflows['failed'], 0)
        self.assertEqual(task_workflows['p95_task_latency_ms'], 120.0)
        self.assertAlmostEqual(task_workflows['tool_error_rate'], 0.5, places=4)
        self.assertEqual(task_workflows['step_events'], 7)
        self.assertEqual(task_workflows['avg_steps_per_task'], 7.0)
        self.assertEqual(task_workflows['review_count'], 1)
        self.assertEqual(task_workflows['review_failed'], 1)
        self.assertEqual(task_workflows['review_fix_rate'], 1.0)
        self.assertEqual(task_workflows['unsupported_claim_rate'], 1.0)
        self.assertEqual(task_workflows['replan_count'], 1)
        self.assertEqual(task_workflows['review_replans'], 1)
        self.assertEqual(task_workflows['avg_plan_version'], 2.0)
        self.assertEqual(task_workflows['artifact_events'], 2)
        self.assertEqual(task_workflows['final_artifact_count'], 1)
        self.assertEqual(task_workflows['avg_artifact_versions'], 2.0)
        self.assertEqual(task_workflows['avg_artifact_memory_count'], 2.0)
        self.assertEqual(task_workflows['avg_task_memory_count'], 5.0)
        self.assertEqual(task_workflows['avg_tool_error_count'], 1.0)
        self.assertEqual(task_workflows['sub_agent_started'], 2)
        self.assertEqual(task_workflows['sub_agent_completed'], 2)
        self.assertEqual(task_workflows['sub_agent_failed'], 0)
        self.assertEqual(task_workflows['avg_sub_agent_runs_per_task'], 2.0)
        self.assertEqual(task_workflows['sub_agent_breakdown']['evidence_agent']['run_count'], 1)
        self.assertEqual(task_workflows['sub_agent_selected_tool_breakdown']['review_report'], 1)
        self.assertEqual(task_workflows['retrieval_events'], 1)
        self.assertEqual(task_workflows['avg_retrieval_candidate_count'], 7.0)
        self.assertEqual(task_workflows['avg_retrieval_selected_count'], 3.0)
        self.assertEqual(task_workflows['retrieval_mode_breakdown']['hybrid_graph'], 1)
        self.assertEqual(task_workflows['rerank_mode_breakdown']['lexical'], 1)
        remote_workers = response.json()['model_runtime']['remote_workers']
        self.assertTrue(remote_workers['knowledge']['ready'])
        self.assertTrue(remote_workers['sandbox']['ready'])
        self.assertEqual(remote_workers['knowledge']['health_endpoint'], 'http://knowledge.test/api/v1/knowledge/health')
        self.assertEqual(remote_workers['sandbox']['health_endpoint'], 'http://sandbox.test/api/v1/sandbox/health')
        self.assertTrue(remote_workers['knowledge']['probed'])
        self.assertTrue(remote_workers['knowledge']['probe_ok'])
        self.assertEqual(remote_workers['knowledge']['probe_status_code'], 200)
        self.assertTrue(remote_workers['sandbox']['probed'])
        self.assertTrue(remote_workers['sandbox']['probe_ok'])
        self.assertEqual(remote_workers['sandbox']['supported_tools_count'], 3)
        self.assertEqual(remote_workers['sandbox']['supported_tools'], ['draft_report', 'finalize_report', 'review_report'])
        capabilities = response.json()['model_runtime']['capabilities']
        self.assertTrue(capabilities['repository']['ready'])
        self.assertTrue(capabilities['api_contract']['ready'])
        self.assertTrue(capabilities['artifact']['ready'])
        self.assertTrue(capabilities['database']['ready'])

    def test_metrics_exposes_flattened_context_compression_indicators(self) -> None:
        """覆盖 `metrics_exposes_flattened_context_compression_indicators` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        with patch(
            'app.api.v1.endpoints.health._probe_remote_worker_sync',
            side_effect=[
                {
                    'probe_enabled': True,
                    'probed': True,
                    'probe_ok': True,
                    'probe_status_code': 200,
                    'probe_latency_ms': 12,
                    'probe_error': None,
                    'probe_response_status': 'ok',
                    'probe_ready': True,
                },
                {
                    'probe_enabled': True,
                    'probed': True,
                    'probe_ok': False,
                    'probe_status_code': 503,
                    'probe_latency_ms': 31,
                    'probe_error': 'upstream_unavailable',
                    'probe_response_status': 'degraded',
                    'probe_ready': False,
                },
            ],
        ):
            response = self.client.get('/api/v1/metrics')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['context_compression_enabled'])
        self.assertEqual(payload['context_compression_requests'], 3)
        self.assertAlmostEqual(payload['context_compression_char_reduction_ratio'], 0.64, places=4)
        self.assertAlmostEqual(payload['context_compression_sentence_reduction_ratio'], 0.6053, places=4)
        self.assertAlmostEqual(payload['context_compression_chunk_reduction_ratio'], 0.5333, places=4)
        self.assertEqual(payload['context_compression_recent_requests'], 3)
        self.assertAlmostEqual(payload['context_compression_recent_char_reduction_ratio'], 0.64, places=4)
        self.assertEqual(payload['semantic_chunking_documents'], 1)
        self.assertEqual(payload['semantic_chunking_requested_documents'], 1)
        self.assertEqual(payload['semantic_chunking_source_segments'], 5)
        self.assertEqual(payload['semantic_chunking_prepared_segments'], 3)
        self.assertEqual(payload['semantic_chunking_semantic_segments'], 2)
        self.assertEqual(payload['semantic_chunking_fixed_segments'], 1)
        self.assertEqual(payload['semantic_chunking_prepared_groups'], 1)
        self.assertEqual(payload['semantic_chunking_merged_source_segments'], 2)
        self.assertAlmostEqual(payload['semantic_chunking_avg_merge_ratio'], 0.4, places=4)
        self.assertEqual(payload['retrieval_enhancement_requests'], 2)
        self.assertEqual(payload['retrieval_question_oriented_requests'], 1)
        self.assertEqual(payload['retrieval_parent_chunk_requests'], 1)
        self.assertEqual(payload['retrieval_multi_vector_hits'], 1)
        self.assertEqual(payload['retrieval_aggregated_targets'], 2)
        self.assertEqual(payload['retrieval_aggregated_away_candidates'], 1)
        self.assertEqual(payload['retrieval_parent_expanded'], 2)
        self.assertEqual(payload['retrieval_parent_document_hits'], 2)
        self.assertEqual(payload['retrieval_recent_requests'], 2)
        self.assertEqual(payload['retrieval_recent_multi_vector_hits'], 1)
        self.assertEqual(payload['graph_node_count'], 1)
        self.assertEqual(payload['graph_edge_count'], 1)
        self.assertEqual(payload['graph_requests'], 1)
        self.assertEqual(payload['graph_candidates'], 2)
        self.assertEqual(payload['graph_seed_node_count'], 2)
        self.assertEqual(payload['graph_expanded_edge_count'], 3)
        self.assertEqual(payload['graph_returned_citations'], 2)
        self.assertAlmostEqual(payload['graph_avg_max_hops'], 2.0, places=4)
        self.assertEqual(payload['graph_recent_requests'], 1)
        self.assertEqual(payload['model_route_selected'], 1)
        self.assertEqual(payload['model_route_consumed'], 1)
        self.assertAlmostEqual(payload['model_route_avg_actual_cost_units'], 0.276, places=4)
        self.assertEqual(payload['model_route_provider_usage_count'], 1)
        self.assertTrue(payload['remote_knowledge_provider_ready'])
        self.assertTrue(payload['remote_knowledge_provider_probed'])
        self.assertTrue(payload['remote_knowledge_provider_probe_ok'])
        self.assertEqual(payload['remote_knowledge_provider_probe_status_code'], 200)
        self.assertEqual(payload['remote_knowledge_provider_probe_latency_ms'], 12)
        self.assertTrue(payload['remote_sandbox_provider_probed'])
        self.assertFalse(payload['remote_sandbox_provider_ready'])
        self.assertFalse(payload['remote_sandbox_provider_probe_ok'])
        self.assertEqual(payload['remote_sandbox_provider_probe_status_code'], 503)
        self.assertEqual(payload['remote_sandbox_provider_probe_latency_ms'], 31)
        self.assertEqual(payload['remote_sandbox_provider_probe_error'], 'upstream_unavailable')
        self.assertEqual(payload['task_workflow_started'], 1)
        self.assertEqual(payload['task_workflow_completed'], 1)
        self.assertEqual(payload['task_workflow_p95_task_latency_ms'], 120.0)
        self.assertAlmostEqual(payload['task_workflow_tool_error_rate'], 0.5, places=4)
        self.assertEqual(payload['task_workflow_step_events'], 7)
        self.assertEqual(payload['task_workflow_avg_steps_per_task'], 7.0)
        self.assertEqual(payload['task_workflow_review_count'], 1)
        self.assertEqual(payload['task_workflow_review_failed'], 1)
        self.assertEqual(payload['task_workflow_review_fix_rate'], 1.0)
        self.assertEqual(payload['task_workflow_unsupported_claim_rate'], 1.0)
        self.assertEqual(payload['task_workflow_replan_count'], 1)
        self.assertEqual(payload['task_workflow_review_replans'], 1)
        self.assertEqual(payload['task_workflow_avg_plan_version'], 2.0)
        self.assertEqual(payload['task_workflow_artifact_events'], 2)
        self.assertEqual(payload['task_workflow_final_artifact_count'], 1)
        self.assertEqual(payload['task_workflow_avg_artifact_versions'], 2.0)
        self.assertEqual(payload['task_workflow_avg_artifact_memory_count'], 2.0)
        self.assertEqual(payload['task_workflow_avg_task_memory_count'], 5.0)
        self.assertEqual(payload['task_workflow_avg_tool_error_count'], 1.0)
        self.assertEqual(payload['task_workflow_sub_agent_started'], 2)
        self.assertEqual(payload['task_workflow_sub_agent_completed'], 2)
        self.assertEqual(payload['task_workflow_sub_agent_failed'], 0)
        self.assertEqual(payload['task_workflow_avg_sub_agent_runs_per_task'], 2.0)
        self.assertEqual(payload['task_workflow_retrieval_events'], 1)
        self.assertEqual(payload['task_workflow_avg_retrieval_candidate_count'], 7.0)
        self.assertEqual(payload['task_workflow_avg_retrieval_selected_count'], 3.0)
        self.assertTrue(payload['semantic_cache_enabled'])
        self.assertGreaterEqual(payload['semantic_cache_entries'], 1)
        self.assertEqual(payload['semantic_cache_lifetime_hits'], 2)
        self.assertEqual(payload['semantic_cache_hits'], 1)
        self.assertEqual(payload['semantic_cache_misses'], 1)
        self.assertAlmostEqual(payload['semantic_cache_hit_rate'], 0.5, places=4)
        self.assertEqual(payload['office_conversion_cache_files'], 2)
        self.assertEqual(payload['office_conversion_cache_bytes'], 10)
        self.assertEqual(payload['office_conversion_cache_max_files'], 2)
        self.assertEqual(payload['office_conversion_cache_ttl_seconds'], 3600)
        self.assertEqual(payload['office_conversion_cache_prune_runs'], 1)
        self.assertEqual(payload['office_conversion_cache_deleted_files'], 1)
        self.assertIsNotNone(payload['office_conversion_cache_last_pruned_at'])

    def test_trace_summary_recent_window_uses_last_n_events(self) -> None:
        """覆盖 `trace_summary_recent_window_uses_last_n_events` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        trace = self.container.trace.__class__()
        for index in range(25):
            trace.record(
                'context_compressed',
                {
                    'enabled': True,
                    'original_chunk_count': 2,
                    'compressed_chunk_count': 1,
                    'original_sentence_count': 4,
                    'compressed_sentence_count': 2,
                    'original_char_count': 100 + index,
                    'compressed_char_count': 50 + index,
                    'strategy': 'sentence_extract' if index % 2 == 0 else 'disabled',
                },
            )

        recent = trace.summarize_context_compression(last_n=20)

        self.assertEqual(recent['compressed_requests'], 20)
        self.assertEqual(recent['strategy_breakdown']['sentence_extract'], 10)
        self.assertEqual(recent['strategy_breakdown']['disabled'], 10)

    def test_trace_summary_exposes_semantic_chunking_and_retrieval_enhancements(self) -> None:
        """覆盖 `trace_summary_exposes_semantic_chunking_and_retrieval_enhancements` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        semantic_chunking = self.container.trace.summarize_semantic_chunking()
        retrieval_enhancements = self.container.trace.summarize_retrieval_enhancements()
        task_workflows = self.container.trace.summarize_task_workflows()

        self.assertEqual(semantic_chunking['documents'], 1)
        self.assertEqual(semantic_chunking['merged_source_segments'], 2)
        self.assertEqual(retrieval_enhancements['requests'], 2)
        self.assertEqual(retrieval_enhancements['multi_vector_hits'], 1)
        self.assertEqual(retrieval_enhancements['parent_document_hits'], 2)
        self.assertEqual(task_workflows['completed'], 1)
        self.assertEqual(task_workflows['review_replans'], 1)
        self.assertEqual(task_workflows['avg_plan_version'], 2.0)
        self.assertEqual(task_workflows['retrieval_mode_breakdown']['hybrid_graph'], 1)


if __name__ == '__main__':
    unittest.main()

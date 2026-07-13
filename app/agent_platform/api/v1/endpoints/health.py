"""健康检查接口模块�?
负责暴露应用可用性检查和运行时聚合指标接口。该模块属于 API 入口层，但会串联多个服务
�?trace 汇总能力，�?LLM、检索、缓存、任务工作流等运行状态折叠为统一观测输出�?"""

import asyncio
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.agent_platform.api.deps import get_container

router = APIRouter()


def _probe_remote_worker_sync(health_endpoint: str, *, timeout_seconds: float, auth_token: str | None = None) -> dict[str, Any]:
    """主动探测远程 worker 健康状态�?
    Args:
        health_endpoint: 远程健康检查地址�?        timeout_seconds: 探测超时时间�?        auth_token: 可�?Bearer Token�?
    Returns:
        包含探测状态码、耗时�?readiness 的结果字典�?    """
    headers: dict[str, str] = {}
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
    started_at = perf_counter()
    try:
        with httpx.Client(timeout=max(1.0, float(timeout_seconds)), headers=headers) as client:
            response = client.get(health_endpoint)
        latency_ms = int((perf_counter() - started_at) * 1000)
        payload: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            payload = {}
        ready = bool(payload.get('ready')) if 'ready' in payload else response.is_success
        return {
            'probe_enabled': True,
            'probed': True,
            'probe_ok': bool(response.is_success and ready),
            'probe_status_code': response.status_code,
            'probe_latency_ms': latency_ms,
            'probe_error': None,
            'probe_response_status': payload.get('status'),
            'probe_ready': ready,
        }
    except httpx.HTTPError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return {
            'probe_enabled': True,
            'probed': True,
            'probe_ok': False,
            'probe_status_code': None,
            'probe_latency_ms': latency_ms,
            'probe_error': str(exc) or exc.__class__.__name__,
            'probe_response_status': None,
            'probe_ready': False,
        }


async def _attach_remote_probe(
    status: dict[str, Any],
    *,
    timeout_seconds: float,
    auth_token: str | None = None,
) -> dict[str, Any]:
    """在配置态状态上附加主动探测结果�?
    Args:
        status: 基于配置推导出的 provider 状态字典�?        timeout_seconds: 探测超时时间�?        auth_token: 可�?Bearer Token�?
    Returns:
        合并配置态和主动探测结果后的状态字典�?    """
    health_endpoint = status.get('health_endpoint')
    if not status.get('remote_enabled'):
        return {
            **status,
            'probe_enabled': False,
            'probed': False,
            'probe_ok': None,
            'probe_status_code': None,
            'probe_latency_ms': None,
            'probe_error': None,
            'probe_response_status': None,
            'probe_ready': status.get('ready'),
        }
    if not health_endpoint:
        return {
            **status,
            'ready': False,
            'probe_enabled': False,
            'probed': False,
            'probe_ok': None,
            'probe_status_code': None,
            'probe_latency_ms': None,
            'probe_error': 'health_endpoint_not_configured',
            'probe_response_status': None,
            'probe_ready': False,
        }
    probe = await asyncio.to_thread(
        _probe_remote_worker_sync,
        str(health_endpoint),
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
    )
    return {
        **status,
        **probe,
        'ready': bool(probe.get('probe_ready')),
    }


def _knowledge_provider_status(settings) -> dict:
    """根据配置生成 knowledge provider 的静态状态描述�?
    Args:
        settings: 全局配置对象�?
    Returns:
        knowledge provider 的配置态状态字典�?    """
    provider = settings.knowledge_capability_provider
    base_url = settings.knowledge_capability_base_url
    remote = provider == 'remote_http'
    return {
        'provider': provider,
        'remote_enabled': remote,
        'configured': (not remote) or bool(base_url),
        'base_url_configured': bool(base_url),
        'base_url': base_url,
        'timeout_seconds': settings.knowledge_capability_timeout_seconds,
        'auth_configured': bool(settings.knowledge_capability_auth_token),
        'allow_local_fallback': settings.knowledge_capability_allow_local_fallback,
        'circuit_breaker_threshold': settings.remote_provider_circuit_breaker_threshold,
        'circuit_breaker_cooldown_seconds': settings.remote_provider_circuit_breaker_cooldown_seconds,
        'health_endpoint': (
            f"{base_url.rstrip('/')}{settings.api_prefix.rstrip('/')}/knowledge/health"
            if remote and base_url
            else None
        ),
        'ready': (not remote) or bool(base_url),
    }


def _sandbox_provider_status(settings, container=None) -> dict:
    """根据配置生成 sandbox provider 的静态状态描述�?
    Args:
        settings: 全局配置对象�?        container: 可选应用容器，用于补充本地工具目录信息�?
    Returns:
        sandbox provider 的配置态状态字典�?    """
    provider = settings.sandbox_executor_provider
    base_url = settings.sandbox_executor_base_url
    remote = provider == 'remote_http'
    catalog = container.local_sandbox_engine.list_worker_tools() if container is not None else None
    return {
        'provider': provider,
        'remote_enabled': remote,
        'configured': (not remote) or bool(base_url),
        'base_url_configured': bool(base_url),
        'base_url': base_url,
        'timeout_seconds': settings.sandbox_executor_timeout_seconds,
        'auth_configured': bool(settings.sandbox_executor_auth_token),
        'allow_local_fallback': settings.sandbox_executor_allow_local_fallback,
        'circuit_breaker_threshold': settings.remote_provider_circuit_breaker_threshold,
        'circuit_breaker_cooldown_seconds': settings.remote_provider_circuit_breaker_cooldown_seconds,
        'health_endpoint': (
            f"{base_url.rstrip('/')}{settings.api_prefix.rstrip('/')}/sandbox/health"
            if remote and base_url
            else None
        ),
        'supported_tools_count': len(catalog.tools) if catalog is not None else 0,
        'supported_tools': [tool.tool_name for tool in catalog.tools] if catalog is not None else [],
        'ready': (not remote) or bool(base_url),
    }


def _flatten_remote_worker_metrics(prefix: str, status: dict[str, Any]) -> dict[str, Any]:
    """�?remote worker 状态压平成适合 /metrics 消费的字段�?""
    return {
        f'{prefix}_provider_ready': status.get('ready'),
        f'{prefix}_provider_remote_enabled': status.get('remote_enabled'),
        f'{prefix}_provider_configured': status.get('configured'),
        f'{prefix}_provider_probed': status.get('probed'),
        f'{prefix}_provider_probe_ok': status.get('probe_ok'),
        f'{prefix}_provider_probe_status_code': status.get('probe_status_code'),
        f'{prefix}_provider_probe_latency_ms': status.get('probe_latency_ms'),
        f'{prefix}_provider_probe_error': status.get('probe_error'),
        f'{prefix}_provider_probe_ready': status.get('probe_ready'),
    }


@router.get('/health')
async def health(request: Request) -> dict:
    """返回应用、模型和向量库的运行状态�?
    Args:
        request: 当前请求对象，用于获取应用级依赖容器�?
    Returns:
        包含应用、模型运行时、缓存和任务工作流状态的健康检查结果�?    """
    container = get_container(request)
    settings = container.settings
    llm = container.query_engine.llm
    embed_model = container.retrieval.embed_model
    rerank = container.retrieval.get_rerank_runtime_status()
    compression_metrics = container.trace.summarize_context_compression()
    recent_compression_metrics = container.trace.summarize_context_compression(last_n=20)
    semantic_chunking_metrics = container.trace.summarize_semantic_chunking()
    recent_semantic_chunking_metrics = container.trace.summarize_semantic_chunking(last_n=20)
    semantic_cache_status = container.semantic_cache.get_runtime_status()
    semantic_cache_metrics = container.trace.summarize_semantic_cache()
    recent_semantic_cache_metrics = container.trace.summarize_semantic_cache(last_n=20)
    retrieval_enhancement_metrics = container.trace.summarize_retrieval_enhancements()
    recent_retrieval_enhancement_metrics = container.trace.summarize_retrieval_enhancements(last_n=20)
    graph_retrieval_metrics = container.trace.summarize_graph_retrieval()
    recent_graph_retrieval_metrics = container.trace.summarize_graph_retrieval(last_n=20)
    knowledge_worker_status, sandbox_worker_status = await asyncio.gather(
        _attach_remote_probe(
            _knowledge_provider_status(settings),
            timeout_seconds=settings.knowledge_capability_timeout_seconds,
            auth_token=settings.knowledge_capability_auth_token,
        ),
        _attach_remote_probe(
            _sandbox_provider_status(settings, container),
            timeout_seconds=settings.sandbox_executor_timeout_seconds,
            auth_token=settings.sandbox_executor_auth_token,
        ),
    )
    model_route_metrics = container.trace.summarize_model_routes()
    recent_model_route_metrics = container.trace.summarize_model_routes(last_n=20)
    task_workflow_metrics = container.trace.summarize_task_workflows()
    recent_task_workflow_metrics = container.trace.summarize_task_workflows(last_n=20)
    office_cache_status = container.ingestion.get_conversion_cache_status()
    # 这里集中聚合多个 runtime status，方便运维和前端在一个接口内完成系统体检�?    return {
        'status': 'ok',
        'app': 'up',
        'chroma': container.vector_store.ping(),
        'sqlite': container.persistence.ping(),
        'model_runtime': {
            'provider': settings.llm_provider,
            'llm': {
                'model': settings.llm_model,
                'api_key_configured': bool(settings.resolved_llm_api_key),
                'base_url_configured': bool(settings.resolved_llm_base_url),
                'runtime_mode': 'llamaindex_llm' if llm is not None else 'local_fallback',
                'runtime_class': llm.__class__.__name__ if llm is not None else None,
            },
            'embedding': {
                'model': settings.embed_model,
                'api_key_configured': bool(settings.resolved_embed_api_key),
                'base_url_configured': bool(settings.resolved_embed_base_url),
                'runtime_mode': 'provider_embedding' if embed_model.__class__.__name__ != 'HashEmbedding' else 'local_hash',
                'runtime_class': embed_model.__class__.__name__,
            },
            'rerank': rerank,
            'context_compression': {
                'enabled_by_default': settings.enable_context_compression,
                'max_chunks': settings.context_compression_max_chunks,
                'max_sentences': settings.context_compression_max_sentences,
                'max_chars': settings.context_compression_max_chars,
                **compression_metrics,
                'recent_window': {
                    'size': 20,
                    **recent_compression_metrics,
                },
            },
            'semantic_chunking': {
                'default_strategy': settings.ingestion_chunking_strategy,
                'buffer_size': settings.semantic_chunk_buffer_size,
                'breakpoint_percentile': settings.semantic_chunk_breakpoint_percentile,
                **semantic_chunking_metrics,
                'recent_window': {
                    'size': 20,
                    **recent_semantic_chunking_metrics,
                },
            },
            'retrieval_enhancements': {
                **retrieval_enhancement_metrics,
                'recent_window': {
                    'size': 20,
                    **recent_retrieval_enhancement_metrics,
                },
            },
            'graph_rag': {
                **container.graph_service.get_runtime_status(),
                **graph_retrieval_metrics,
                'recent_window': {
                    'size': 20,
                    **recent_graph_retrieval_metrics,
                },
            },
            'model_routing': {
                **model_route_metrics,
                'recent_window': {
                    'size': 20,
                    **recent_model_route_metrics,
                },
            },
            'semantic_cache': {
                **semantic_cache_status,
                **semantic_cache_metrics,
                'recent_window': {
                    'size': 20,
                    **recent_semantic_cache_metrics,
                },
            },
            'task_workflows': {
                **task_workflow_metrics,
                'task_count': len(container.state.tasks),
                'artifact_count': len(container.state.artifacts),
                'recent_window': {
                    'size': 20,
                    **recent_task_workflow_metrics,
                },
            },
            'office_conversion_cache': office_cache_status,
            'remote_workers': {
                'knowledge': knowledge_worker_status,
                'sandbox': sandbox_worker_status,
            },
            'capabilities': {
                'api_contract': {
                    'ready': hasattr(container, 'local_api_contract_capability'),
                    'root_path': (
                        str(container.local_api_contract_capability.root_path)
                        if hasattr(container, 'local_api_contract_capability')
                        else None
                    ),
                },
                'artifact': {
                    'ready': hasattr(container, 'local_artifact_capability'),
                    'artifact_count': len(container.state.artifacts) if hasattr(container, 'state') else 0,
                },
                'database': {
                    'ready': hasattr(container, 'local_database_capability'),
                    'db_path': (
                        str(container.local_database_capability.db_path)
                        if hasattr(container, 'local_database_capability')
                        else None
                    ),
                },
                'repository': {
                    'ready': hasattr(container, 'local_repository_capability'),
                    'root_path': (
                        str(container.local_repository_capability.root_path)
                        if hasattr(container, 'local_repository_capability')
                        else None
                    ),
                }
            },
        },
        'ragas': container.eval_service.get_runtime_status(),
    }


@router.get('/metrics')
async def metrics(request: Request) -> dict:
    """返回用于观测系统规模与能力状态的聚合指标�?
    Args:
        request: 当前请求对象，用于获取应用级依赖容器�?
    Returns:
        面向监控与排障的聚合指标字典�?    """
    container = get_container(request)
    eval_runtime = container.eval_service.get_runtime_status()
    rerank_runtime = container.retrieval.get_rerank_runtime_status()
    compression_metrics = container.trace.summarize_context_compression()
    recent_compression_metrics = container.trace.summarize_context_compression(last_n=20)
    semantic_chunking_metrics = container.trace.summarize_semantic_chunking()
    recent_semantic_chunking_metrics = container.trace.summarize_semantic_chunking(last_n=20)
    semantic_cache_status = container.semantic_cache.get_runtime_status()
    semantic_cache_metrics = container.trace.summarize_semantic_cache()
    recent_semantic_cache_metrics = container.trace.summarize_semantic_cache(last_n=20)
    retrieval_enhancement_metrics = container.trace.summarize_retrieval_enhancements()
    recent_retrieval_enhancement_metrics = container.trace.summarize_retrieval_enhancements(last_n=20)
    graph_retrieval_metrics = container.trace.summarize_graph_retrieval()
    recent_graph_retrieval_metrics = container.trace.summarize_graph_retrieval(last_n=20)
    knowledge_worker_status, sandbox_worker_status = await asyncio.gather(
        _attach_remote_probe(
            _knowledge_provider_status(container.settings),
            timeout_seconds=container.settings.knowledge_capability_timeout_seconds,
            auth_token=container.settings.knowledge_capability_auth_token,
        ),
        _attach_remote_probe(
            _sandbox_provider_status(container.settings, container),
            timeout_seconds=container.settings.sandbox_executor_timeout_seconds,
            auth_token=container.settings.sandbox_executor_auth_token,
        ),
    )
    model_route_metrics = container.trace.summarize_model_routes()
    recent_model_route_metrics = container.trace.summarize_model_routes(last_n=20)
    task_workflow_metrics = container.trace.summarize_task_workflows()
    recent_task_workflow_metrics = container.trace.summarize_task_workflows(last_n=20)
    office_cache_status = container.ingestion.get_conversion_cache_status()
    # `/metrics` 输出保持扁平结构，便于监控系统直接采集和做阈值告警�?    return {
        'collections': len(container.state.collections),
        'documents': len(container.state.documents),
        'tasks': len(container.state.tasks),
        'artifacts': len(container.state.artifacts),
        'sessions': len(container.state.sessions),
        'feedback_items': len(container.state.feedback_items),
        'eval_candidates': len(container.state.eval_candidates),
        'eval_tasks': len(container.state.eval_tasks),
        'llm_mode': 'llamaindex_llm' if container.query_engine.llm is not None else 'local_fallback',
        'embedding_mode': (
            'provider_embedding'
            if container.retrieval.embed_model.__class__.__name__ != 'HashEmbedding'
            else 'local_hash'
        ),
        'rerank_mode': rerank_runtime['runtime_mode'],
        'ragas_ready': eval_runtime['ready'],
        'context_compression_enabled': container.settings.enable_context_compression,
        'context_compression_requests': compression_metrics['compressed_requests'],
        'context_compression_char_reduction_ratio': compression_metrics['avg_char_reduction_ratio'],
        'context_compression_sentence_reduction_ratio': compression_metrics['avg_sentence_reduction_ratio'],
        'context_compression_chunk_reduction_ratio': compression_metrics['avg_chunk_reduction_ratio'],
        'context_compression_recent_requests': recent_compression_metrics['compressed_requests'],
        'context_compression_recent_char_reduction_ratio': recent_compression_metrics['avg_char_reduction_ratio'],
        'context_compression_recent_sentence_reduction_ratio': recent_compression_metrics['avg_sentence_reduction_ratio'],
        'context_compression_recent_chunk_reduction_ratio': recent_compression_metrics['avg_chunk_reduction_ratio'],
        'semantic_chunking_strategy': container.settings.ingestion_chunking_strategy,
        'semantic_chunking_documents': semantic_chunking_metrics['documents'],
        'semantic_chunking_requested_documents': semantic_chunking_metrics['requested_semantic_documents'],
        'semantic_chunking_source_segments': semantic_chunking_metrics['source_segments'],
        'semantic_chunking_prepared_segments': semantic_chunking_metrics['prepared_segments'],
        'semantic_chunking_semantic_segments': semantic_chunking_metrics['semantic_segments'],
        'semantic_chunking_fixed_segments': semantic_chunking_metrics['fixed_segments'],
        'semantic_chunking_prepared_groups': semantic_chunking_metrics['prepared_groups'],
        'semantic_chunking_merged_source_segments': semantic_chunking_metrics['merged_source_segments'],
        'semantic_chunking_avg_merge_ratio': semantic_chunking_metrics['avg_merge_ratio'],
        'semantic_chunking_recent_documents': recent_semantic_chunking_metrics['documents'],
        'retrieval_enhancement_requests': retrieval_enhancement_metrics['requests'],
        'retrieval_question_oriented_requests': retrieval_enhancement_metrics['question_oriented_requests'],
        'retrieval_parent_chunk_requests': retrieval_enhancement_metrics['parent_chunk_requests'],
        'retrieval_multi_vector_hits': retrieval_enhancement_metrics['multi_vector_hits'],
        'retrieval_aggregated_targets': retrieval_enhancement_metrics['aggregated_targets'],
        'retrieval_aggregated_away_candidates': retrieval_enhancement_metrics['aggregated_away_candidates'],
        'retrieval_parent_expanded': retrieval_enhancement_metrics['parent_expanded'],
        'retrieval_parent_document_hits': retrieval_enhancement_metrics['parent_document_hits'],
        'retrieval_recent_requests': recent_retrieval_enhancement_metrics['requests'],
        'retrieval_recent_multi_vector_hits': recent_retrieval_enhancement_metrics['multi_vector_hits'],
        'graph_node_count': container.graph_service.get_runtime_status()['node_count'],
        'graph_edge_count': container.graph_service.get_runtime_status()['edge_count'],
        'graph_requests': graph_retrieval_metrics['graph_requests'],
        'graph_candidates': graph_retrieval_metrics['graph_candidates'],
        'graph_seed_node_count': graph_retrieval_metrics['graph_seed_node_count'],
        'graph_expanded_edge_count': graph_retrieval_metrics['graph_expanded_edge_count'],
        'graph_returned_citations': graph_retrieval_metrics['graph_returned_citations'],
        'graph_avg_max_hops': graph_retrieval_metrics['avg_graph_max_hops'],
        'graph_recent_requests': recent_graph_retrieval_metrics['graph_requests'],
        'model_route_selected': model_route_metrics['selected'],
        'model_route_consumed': model_route_metrics['consumed'],
        'model_route_llm_selected': model_route_metrics['llm_selected'],
        'model_route_avg_estimated_cost_units': model_route_metrics['avg_estimated_cost_units'],
        'model_route_avg_actual_cost_units': model_route_metrics['avg_actual_cost_units'],
        'model_route_total_actual_cost_units': model_route_metrics['total_actual_cost_units'],
        'model_route_avg_total_tokens': model_route_metrics['avg_total_tokens'],
        'model_route_provider_reported_count': model_route_metrics['provider_reported_count'],
        'model_route_provider_cost_count': model_route_metrics['provider_cost_count'],
        'model_route_provider_usage_count': model_route_metrics['provider_usage_count'],
        'model_route_local_estimate_count': model_route_metrics['local_estimate_count'],
        'model_route_recent_consumed': recent_model_route_metrics['consumed'],
        **_flatten_remote_worker_metrics('remote_knowledge', knowledge_worker_status),
        **_flatten_remote_worker_metrics('remote_sandbox', sandbox_worker_status),
        'task_workflow_started': task_workflow_metrics['started'],
        'task_workflow_completed': task_workflow_metrics['completed'],
        'task_workflow_failed': task_workflow_metrics['failed'],
        'task_workflow_success_rate': task_workflow_metrics['success_rate'],
        'task_workflow_avg_latency_ms': task_workflow_metrics['avg_latency_ms'],
        'task_workflow_p95_task_latency_ms': task_workflow_metrics['p95_task_latency_ms'],
        'task_workflow_tool_calls': task_workflow_metrics['tool_calls'],
        'task_workflow_tool_error_rate': task_workflow_metrics['tool_error_rate'],
        'task_workflow_step_events': task_workflow_metrics['step_events'],
        'task_workflow_avg_steps_per_task': task_workflow_metrics['avg_steps_per_task'],
        'task_workflow_step_failure_rate': task_workflow_metrics['step_failure_rate'],
        'task_workflow_review_count': task_workflow_metrics['review_count'],
        'task_workflow_review_failed': task_workflow_metrics['review_failed'],
        'task_workflow_review_fix_rate': task_workflow_metrics['review_fix_rate'],
        'task_workflow_unsupported_claim_rate': task_workflow_metrics['unsupported_claim_rate'],
        'task_workflow_replan_count': task_workflow_metrics['replan_count'],
        'task_workflow_evidence_gap_replans': task_workflow_metrics['evidence_gap_replans'],
        'task_workflow_review_replans': task_workflow_metrics['review_replans'],
        'task_workflow_avg_plan_version': task_workflow_metrics['avg_plan_version'],
        'task_workflow_artifact_events': task_workflow_metrics['artifact_events'],
        'task_workflow_final_artifact_count': task_workflow_metrics['final_artifact_count'],
        'task_workflow_avg_artifact_versions': task_workflow_metrics['avg_artifact_versions'],
        'task_workflow_avg_artifact_memory_count': task_workflow_metrics['avg_artifact_memory_count'],
        'task_workflow_avg_task_memory_count': task_workflow_metrics['avg_task_memory_count'],
        'task_workflow_avg_tool_error_count': task_workflow_metrics['avg_tool_error_count'],
        'task_workflow_avg_cost_per_task': task_workflow_metrics['avg_cost_per_task'],
        'task_workflow_sub_agent_started': task_workflow_metrics['sub_agent_started'],
        'task_workflow_sub_agent_completed': task_workflow_metrics['sub_agent_completed'],
        'task_workflow_sub_agent_failed': task_workflow_metrics['sub_agent_failed'],
        'task_workflow_sub_agent_failure_rate': task_workflow_metrics['sub_agent_failure_rate'],
        'task_workflow_avg_sub_agent_runs_per_task': task_workflow_metrics['avg_sub_agent_runs_per_task'],
        'task_workflow_retrieval_events': task_workflow_metrics['retrieval_events'],
        'task_workflow_avg_retrieval_candidate_count': task_workflow_metrics['avg_retrieval_candidate_count'],
        'task_workflow_avg_retrieval_selected_count': task_workflow_metrics['avg_retrieval_selected_count'],
        'task_workflow_recent_completed': recent_task_workflow_metrics['completed'],
        'task_workflow_recent_success_rate': recent_task_workflow_metrics['success_rate'],
        'semantic_cache_enabled': semantic_cache_status['enabled_by_default'],
        'semantic_cache_entries': semantic_cache_status['entry_count'],
        'semantic_cache_lifetime_hits': semantic_cache_status['lifetime_hits'],
        'semantic_cache_lookups': semantic_cache_metrics['lookups'],
        'semantic_cache_hits': semantic_cache_metrics['hits'],
        'semantic_cache_misses': semantic_cache_metrics['misses'],
        'semantic_cache_hit_rate': semantic_cache_metrics['hit_rate'],
        'semantic_cache_avg_hit_similarity': semantic_cache_metrics['avg_hit_similarity'],
        'semantic_cache_invalidations': semantic_cache_metrics['invalidations'],
        'semantic_cache_recent_lookups': recent_semantic_cache_metrics['lookups'],
        'semantic_cache_recent_hit_rate': recent_semantic_cache_metrics['hit_rate'],
        'office_conversion_cache_files': office_cache_status['file_count'],
        'office_conversion_cache_bytes': office_cache_status['total_bytes'],
        'office_conversion_cache_max_files': office_cache_status['max_files'],
        'office_conversion_cache_ttl_seconds': office_cache_status['ttl_seconds'],
        'office_conversion_cache_prune_runs': office_cache_status['prune_runs'],
        'office_conversion_cache_deleted_files': office_cache_status['deleted_files'],
        'office_conversion_cache_last_pruned_at': office_cache_status['last_pruned_at'],
    }

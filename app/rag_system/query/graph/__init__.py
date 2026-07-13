"""RAG 系统 LangGraph 工作流模块。

提供基于 LangGraph 的有状态查询编排能力，将检索问答链路拆解为细粒度步骤，
支持条件路由、Self-RAG 反思、语义缓存短路、SSE 事件流、checkpoint 等功能。

该模块不依赖主应用的 harness 基础设施，仅依赖 rag_system 自身的组件。
"""

from __future__ import annotations

from app.rag_system.query.graph.adapter import RagQueryEngineAdapter
from app.rag_system.query.graph.events import (
    append_answer_completed_event,
    append_answer_started_event,
    append_cache_hit_event,
    append_checkpoint_created_event,
    append_citation_ready_event,
    append_corrective_check_event,
    append_delta_events,
    append_done_event,
    append_hyde_event,
    append_multi_query_event,
    append_multi_rewrite_event,
    append_retrieval_event,
    append_rewrite_event,
    append_step_completed_event,
    append_step_failed_event,
    append_step_started_event,
    append_start_event,
    make_error_event,
    make_event,
)
from app.rag_system.query.graph.graph import RagQueryGraphBuilder, QueryGraphNodeExecutionError
from app.rag_system.query.graph.nodes import RagQueryGraphNodes
from app.rag_system.query.graph.runtime import RagGraphRuntime
from app.rag_system.query.graph.state import (
    QUERY_GRAPH_ENTRY_ROUTES,
    QueryGraphState,
    QueryGraphUpdate,
    WorkflowMode,
    init_query_graph_state,
)
from app.rag_system.query.graph.step_lifecycle import (
    create_checkpoint_record,
    create_run_event,
    dump_step_runtimes,
    mark_step_completed,
    mark_step_failed,
    mark_step_started,
    normalize_step_runtimes,
)

__all__ = [
    # 适配器
    'RagQueryEngineAdapter',
    # 状态与模式
    'QueryGraphState',
    'QueryGraphUpdate',
    'WorkflowMode',
    'init_query_graph_state',
    'QUERY_GRAPH_ENTRY_ROUTES',
    # 运行时协议
    'RagGraphRuntime',
    # 图构建
    'RagQueryGraphBuilder',
    'RagQueryGraphNodes',
    # 步骤生命周期
    'create_run_event',
    'create_checkpoint_record',
    'mark_step_started',
    'mark_step_completed',
    'mark_step_failed',
    'dump_step_runtimes',
    'normalize_step_runtimes',
    # SSE 事件
    'make_event',
    'make_error_event',
    'append_start_event',
    'append_step_started_event',
    'append_step_completed_event',
    'append_step_failed_event',
    'append_checkpoint_created_event',
    'append_rewrite_event',
    'append_multi_rewrite_event',
    'append_multi_query_event',
    'append_hyde_event',
    'append_cache_hit_event',
    'append_retrieval_event',
    'append_citation_ready_event',
    'append_answer_started_event',
    'append_corrective_check_event',
    'append_delta_events',
    'append_answer_completed_event',
    'append_done_event',
]

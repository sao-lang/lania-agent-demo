"""查询请求到 TaskSpec 的适配模块。

负责把 query/chat 两类入口请求转换为平台统一的 `TaskSpec`。该模块不执行检索和回答逻辑，
只负责把用户请求翻译成 workflow 可消费的任务目标、输入载荷、执行预算和步骤声明。
"""

from __future__ import annotations

from typing import Any

from app.models.query import ChatRequest, QueryRequest
from app.models.task import RunBudget, StepSpec, TaskSpec
from app.workflows.query_state import WorkflowMode


def build_query_task_spec(request: QueryRequest | ChatRequest, mode: WorkflowMode) -> TaskSpec:
    """把 query/chat 请求投影为平台层 TaskSpec。

    Args:
        request: 查询或会话请求对象。
        mode: 当前 workflow 模式，用于区分单轮问答与多轮会话。

    Returns:
        可供 query workflow 直接消费的任务规格对象。
    """

    normalized_mode = _normalize_mode(mode)
    steps = _build_step_specs(request, normalized_mode)
    return TaskSpec(
        task_type=normalized_mode,
        objective=_build_objective(request, normalized_mode),
        input_payload=_build_input_payload(request, normalized_mode),
        run_budget=_build_run_budget(request, steps),
        steps=steps,
        success_criteria=_build_success_criteria(normalized_mode),
    )


def _normalize_mode(mode: WorkflowMode) -> str:
    """把 workflow mode 归并为平台层任务类型。

    这里会把流式与非流式模式收敛到同一个任务语义，避免 TaskSpec 被输出方式影响。
    """
    if mode in {'query', 'query_stream'}:
        return 'grounded_query'
    return 'session_chat'


def _build_objective(request: QueryRequest | ChatRequest, normalized_mode: str) -> str:
    """构造任务目标描述文本。

    目标文本主要用于 TaskSpec 的可读性表达，帮助 runtime、trace 和调试界面快速理解本轮
    任务究竟是“基于会话回答”还是“基于证据回答”。
    """
    if normalized_mode == 'session_chat':
        return f'基于会话上下文回答问题：{request.question.strip()}'
    return f'基于知识证据回答问题：{request.question.strip()}'


def _build_input_payload(request: QueryRequest | ChatRequest, normalized_mode: str) -> dict[str, Any]:
    """整理任务输入载荷。

    载荷中既保留通用查询元信息，也显式展开检索相关开关，便于后续节点按声明式配置读取执行
    策略，而不是再次回看原始请求对象。
    """
    return {
        'entry_mode': normalized_mode,
        'collection_name': request.collection_name,
        'question': request.question.strip(),
        'session_id': request.session_id,
        'filters': request.filters,
        'permission_scope': request.permission_scope,
        'allowed_permissions': list(request.allowed_permissions or []),
        'retrieval_options': {
            'top_k': request.top_k,
            'use_query_rewrite': request.use_query_rewrite,
            'use_multi_query': request.use_multi_query,
            'use_multi_rewrite': request.use_multi_rewrite,
            'use_hybrid_retrieval': request.use_hybrid_retrieval,
            'use_rerank': request.use_rerank,
            'use_hyde': request.use_hyde,
            'use_long_context_reorder': request.use_long_context_reorder,
            'use_context_compression': request.use_context_compression,
            'use_parent_chunk_retrieval': request.use_parent_chunk_retrieval,
            'use_question_oriented_index': request.use_question_oriented_index,
            'use_corrective_rag': request.use_corrective_rag,
            'use_graph_rag': request.use_graph_rag,
            'graph_max_hops': request.graph_max_hops,
            'graph_top_k': request.graph_top_k,
            'graph_entity_types': list(request.graph_entity_types or []),
        },
    }


def _build_run_budget(request: QueryRequest | ChatRequest, steps: list[StepSpec]) -> RunBudget:
    """根据步骤数估算本次 query workflow 的运行预算。"""
    max_steps = max(1, len(steps))
    return RunBudget(
        max_steps=max_steps,
        max_step_turns=2,
        max_tool_calls=max_steps * 2,
        top_k=request.top_k,
    )


def _build_step_specs(request: QueryRequest | ChatRequest, normalized_mode: str) -> list[StepSpec]:
    """按 query workflow 预期链路生成步骤声明。

    该函数把 query graph 中会实际执行的节点映射成 `StepSpec` 列表。这样做的目的是让图编排、
    运行时追踪和任务契约在步骤层拥有同一份声明来源。
    """
    steps: list[StepSpec] = [
        StepSpec(
            step_id='check_guardrails',
            objective='执行输入护栏检查并生成可安全消费的问题版本',
            allowed_tools=[],
            max_turns=1,
            success_criteria=['guardrail decision returned'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='abort',
            output_schema={'entry_mode': normalized_mode},
        )
    ]
    # 多轮会话模式需要先装载历史上下文；单轮问答则直接进入检索准备。
    if normalized_mode == 'session_chat':
        steps.append(
            StepSpec(
                step_id='load_session_context',
                objective='加载并裁剪当前会话上下文',
                allowed_tools=['rag_load_document_context'],
                max_turns=1,
                success_criteria=['session context available'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'session_id': request.session_id},
            )
        )
    # 主链路固定遵循“问题规范化 -> 扩展检索 -> 缓存 -> 检索 -> 压缩上下文 -> 生成答案 -> 自反思”。
    steps.extend(
        [
            StepSpec(
                step_id='rewrite_query',
                objective='规范化问题并生成主检索问题',
                allowed_tools=[],
                max_turns=1,
                success_criteria=['retrieval seed prepared'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'use_query_rewrite': request.use_query_rewrite},
            ),
            StepSpec(
                step_id='expand_queries',
                objective='按检索策略扩展候选检索问题集',
                allowed_tools=[],
                max_turns=1,
                success_criteria=['retrieval questions prepared'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={
                    'use_multi_query': request.use_multi_query,
                    'use_multi_rewrite': request.use_multi_rewrite,
                    'use_hyde': request.use_hyde,
                },
            ),
            StepSpec(
                step_id='lookup_cache',
                objective='优先复用可接受的语义缓存结果',
                allowed_tools=[],
                max_turns=1,
                success_criteria=['cache checked'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'cache_scope': normalized_mode},
            ),
            StepSpec(
                step_id='retrieve_evidence',
                objective='检索并筛选可支撑回答的证据',
                allowed_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
                max_turns=2,
                success_criteria=['evidence pack grounded'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='skip_with_gap',
                output_schema={'top_k': request.top_k, 'use_graph_rag': request.use_graph_rag},
            ),
            StepSpec(
                step_id='compress_context',
                objective='整理证据上下文供回答阶段消费',
                allowed_tools=[],
                max_turns=1,
                success_criteria=['answer context prepared'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'use_context_compression': request.use_context_compression},
            ),
            StepSpec(
                step_id='grounded_answer',
                objective='基于证据生成受控回答',
                allowed_tools=['rag_grounded_answer'],
                max_turns=2,
                success_criteria=['grounded answer returned'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'response_type': 'QueryResponse'},
            ),
            StepSpec(
                step_id='self_reflect',
                objective='检查答案 grounding 质量并决定是否重试或保守改写',
                allowed_tools=[],
                max_turns=1,
                success_criteria=['reflection decision returned'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'use_corrective_rag': request.use_corrective_rag},
            ),
        ]
    )
    # 仅会话模式需要在回答完成后把消息写回 session，并触发自动摘要维护。
    if normalized_mode == 'session_chat':
        steps.append(
            StepSpec(
                step_id='persist_session',
                objective='提交本轮会话结果并更新摘要',
                allowed_tools=[],
                max_turns=1,
                success_criteria=['session persisted'],
                stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
                fallback_action='degrade',
                output_schema={'session_id': request.session_id},
            )
        )
    return steps


def _build_success_criteria(normalized_mode: str) -> list[str]:
    """生成任务级成功条件声明。"""
    if normalized_mode == 'session_chat':
        return ['return grounded chat response', 'session history updated']
    return ['return grounded response', 'response contains citations or explicit evidence gap']

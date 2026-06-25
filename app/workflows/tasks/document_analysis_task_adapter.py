"""文档分析请求到 TaskSpec 的适配模块。

负责把任务接口层传入的 `TaskRequest` 转换为文档分析 workflow 可直接执行的 `TaskSpec`。
模块本身不承载计划生成、证据检索或报告写作逻辑，只负责声明任务目标、输入载荷、运行预算
和步骤序列。
"""

from __future__ import annotations

from app.models.task import StepSpec, TaskRequest, TaskSpec
from app.workflows.tasks.builtin_skills import BuiltinTaskSkillSpec, get_builtin_task_skill_spec


def build_document_analysis_task_spec(request: TaskRequest) -> TaskSpec:
    """为 `document_analysis` workflow 生成运行时步骤版 `TaskSpec`。"""
    skill_spec = get_builtin_task_skill_spec('document_analysis')
    if skill_spec is None:
        raise RuntimeError('missing builtin task skill spec: document_analysis')
    return build_structured_document_task_spec(request, skill_spec=skill_spec)


def build_task_spec_for_request(request: TaskRequest) -> TaskSpec:
    """按 `task_type` 构建统一 runtime `TaskSpec`。

    对已注册的结构化文档类 skill，复用统一步骤模板；对未知 task_type，则回退成只有元数据和
    预算、但不声明步骤的通用 TaskSpec，交由上层决定是否继续处理。
    """
    skill_spec = get_builtin_task_skill_spec(request.task_type)
    if skill_spec is not None:
        return build_structured_document_task_spec(request, skill_spec=skill_spec)
    return TaskSpec(
        task_type=request.task_type,
        objective=request.instructions.strip(),
        input_payload={
            'skill_name': request.task_type,
            'collection_name': request.collection_name,
            'doc_ids': list(request.doc_ids),
            'instructions': request.instructions.strip(),
            'output_format': request.output_format,
            'organization_id': request.organization_id,
            'tenant_id': request.tenant_id,
            'requester_role': request.requester_role,
            'permission_scope': request.permission_scope,
            'allowed_permissions': list(request.allowed_permissions),
        },
        run_budget=request.to_run_budget(),
        steps=[],
        success_criteria=[],
    )


def build_structured_document_task_spec(
    request: TaskRequest,
    *,
    skill_spec: BuiltinTaskSkillSpec,
) -> TaskSpec:
    """为结构化文档类 skill 生成运行时步骤版 `TaskSpec`。

    Args:
        request: 原始任务请求。
        skill_spec: 当前 task_type 对应的内建 skill 静态定义。

    Returns:
        包含结构化步骤声明的任务规格对象。
    """

    steps = _build_runtime_steps(request, skill_spec=skill_spec)
    return TaskSpec(
        task_type=request.task_type,
        objective=f'围绕给定文档集合输出 {request.output_format} 格式的{skill_spec.output_label}：{request.instructions.strip()}',
        input_payload={
            'skill_name': skill_spec.skill_name,
            'collection_name': request.collection_name,
            'doc_ids': list(request.doc_ids),
            'instructions': request.instructions.strip(),
            'output_format': request.output_format,
            'organization_id': request.organization_id,
            'tenant_id': request.tenant_id,
            'requester_role': request.requester_role,
            'permission_scope': request.permission_scope,
            'allowed_permissions': list(request.allowed_permissions),
            'plan_kind': skill_spec.plan_kind,
        },
        run_budget=request.to_run_budget(),
        steps=steps,
        success_criteria=[
            f'生成结构化{skill_spec.output_label}',
            '最终报告字段完整或显式披露证据缺口',
        ],
    )


def _build_runtime_steps(request: TaskRequest, *, skill_spec: BuiltinTaskSkillSpec) -> list[StepSpec]:
    """按结构化文档处理链路构造步骤列表。

    步骤设计遵循“载入任务 -> 计划 -> 取上下文/证据 -> 分析 -> 起草 -> 审查 -> 修订 ->
    退出评估 -> 最终提交”的主链路，以便图编排、trace 和任务契约保持一致。
    """
    return [
        StepSpec(
            step_id='load_task',
            objective='加载任务请求并初始化任务运行时',
            allowed_tools=[],
            max_turns=1,
            success_criteria=['task runtime initialized'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='abort',
            output_schema={'task_type': request.task_type},
        ),
        StepSpec(
            step_id='plan_task',
            objective='生成有界执行计划与关注维度',
            allowed_tools=[],
            max_turns=1,
            success_criteria=['task plan prepared'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='abort',
            output_schema={'plan_kind': skill_spec.plan_kind},
        ),
        StepSpec(
            step_id='collect_document_context',
            objective='读取任务关联文档的基础上下文',
            allowed_tools=['rag_load_document_context'],
            max_turns=1,
            success_criteria=['document context available'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='abort',
            output_schema={'produced_artifacts': ['document_context']},
        ),
        StepSpec(
            step_id='retrieve_evidence',
            objective='收集支撑分析结论的证据片段',
            allowed_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
            max_turns=2,
            success_criteria=['evidence pack prepared'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='skip_with_gap',
            output_schema={'produced_artifacts': ['evidence_pack'], 'top_k': request.constraints.top_k},
        ),
        StepSpec(
            step_id='handle_evidence_gap',
            objective='对证据缺口进行局部重规划并显式保留缺口',
            allowed_tools=['rag_retrieve_graph_evidence', 'extract_key_points', 'extract_risks'],
            max_turns=2,
            success_criteria=['evidence gap disclosed'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='skip_with_gap',
            output_schema={'produced_artifacts': ['evidence_gap_note', 'analysis_result']},
        ),
        StepSpec(
            step_id='analyze',
            objective='提炼关键发现、风险点和待确认问题',
            allowed_tools=['extract_key_points', 'extract_risks'],
            max_turns=2,
            success_criteria=['analysis result prepared'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='degrade',
            output_schema={'produced_artifacts': ['analysis_result', 'risk_list']},
        ),
        StepSpec(
            step_id='draft_artifact',
            objective='生成首版结构化分析报告草稿',
            allowed_tools=['draft_report'],
            max_turns=2,
            success_criteria=['report draft prepared'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='fallback',
            output_schema={'produced_artifacts': ['report_draft']},
        ),
        StepSpec(
            step_id='review_artifact',
            objective='审查报告完整性与证据支撑质量',
            allowed_tools=['review_report'],
            max_turns=2,
            success_criteria=['review result prepared'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='degrade',
            output_schema={'produced_artifacts': ['review_result']},
        ),
        StepSpec(
            step_id='revise_artifact',
            objective='按审查反馈修订报告草稿',
            allowed_tools=['draft_report', 'review_report'],
            max_turns=2,
            success_criteria=['revised report draft prepared'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='fallback',
            output_schema={'produced_artifacts': ['revised_report_draft']},
        ),
        StepSpec(
            step_id='evaluate_exit_criteria',
            objective='评估退出条件并决定是否继续修订',
            allowed_tools=[],
            max_turns=1,
            success_criteria=['exit decision returned'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='degrade',
            output_schema={'produced_artifacts': ['exit_decision']},
        ),
        StepSpec(
            step_id='finalize',
            objective='提交最终 artifact 并完成任务',
            allowed_tools=['finalize_report'],
            max_turns=1,
            success_criteria=['final artifact committed'],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action='abort',
            output_schema={'produced_artifacts': [skill_spec.artifact_type]},
        ),
    ]

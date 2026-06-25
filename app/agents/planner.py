"""任务规划器模块。

负责把文档分析类任务请求收敛成有界执行计划，并在证据缺口、审查失败等场景下生成一次
局部重规划结果。该模块位于任务工作流前段，为节点编排层提供稳定、可追踪的步骤定义。
"""

from __future__ import annotations

import re

from app.models.task import TaskPlan, TaskRequest, TaskStep
from app.workflows.tasks.builtin_skills import get_builtin_task_skill_spec


class TaskPlanner:
    """把任务请求转换为有界的执行计划。"""

    DEFAULT_ASPECTS = ['核心模块', '接口依赖', '风险点', '未决问题']

    def derive_focus_aspects(self, instructions: str) -> list[str]:
        """从任务指令中提取关注维度。

        Args:
            instructions: 用户提交的任务说明文本。

        Returns:
            去重并裁剪后的关注维度列表；无法提取时返回默认维度。
        """

        normalized = re.sub(r'[；;。\n]+', '，', instructions)
        normalized = normalized.replace('以及', '，').replace('和', '，').replace('与', '，')
        segments = [item.strip(' ，,') for item in re.split(r'[，,]', normalized) if item.strip(' ，,')]
        aspects: list[str] = []
        seen: set[str] = set()
        for item in segments:
            if len(item) < 2:
                continue
            if item in seen:
                continue
            aspects.append(item)
            seen.add(item)
        if aspects:
            return aspects[:6]
        return list(self.DEFAULT_ASPECTS)

    def plan(self, request: TaskRequest) -> TaskPlan:
        """生成固定上界的文档分析任务计划。

        Args:
            request: 任务请求对象，包含任务目标与执行约束。

        Returns:
            按固定步骤模板生成的任务计划对象。
        """

        focus_aspects = self.derive_focus_aspects(request.instructions)
        skill_spec = get_builtin_task_skill_spec(request.task_type) or get_builtin_task_skill_spec('document_analysis')
        expected_artifact = skill_spec.artifact_type if skill_spec is not None else 'document_analysis_report'
        goal = f'完成{skill_spec.display_name}' if skill_spec is not None else '完成文档分析报告'
        return TaskPlan(
            goal=goal,
            expected_artifact=expected_artifact,
            max_steps=request.constraints.max_steps,
            steps=[
                TaskStep(
                    step_id='s1',
                    intent='读取任务与文档上下文',
                    tool_name='rag_load_document_context',
                    required_inputs=['task_request.collection_name', 'task_request.doc_ids'],
                    candidate_tools=['rag_load_document_context'],
                    produced_artifacts=['document_context'],
                    failure_branch='abort',
                    success_condition='获得文档摘要、标题和主要章节结构',
                ),
                TaskStep(
                    step_id='s2',
                    intent='收集与任务目标相关的证据',
                    tool_name='rag_retrieve_evidence',
                    required_inputs=['task_request.instructions', 'document_context'],
                    candidate_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
                    produced_artifacts=['evidence_pack'],
                    failure_branch='skip_with_gap',
                    success_condition='获得覆盖关键维度的证据引用片段',
                ),
                TaskStep(
                    step_id='s3',
                    intent='提炼关键发现与风险',
                    tool_name='extract_key_points + extract_risks',
                    required_inputs=['document_context', 'evidence_pack'],
                    candidate_tools=['extract_key_points', 'extract_risks'],
                    produced_artifacts=['analysis_result', 'risk_list'],
                    failure_branch='degrade',
                    success_condition='生成结构化 findings、risks 与 open_questions',
                ),
                TaskStep(
                    step_id='s4',
                    intent='生成并审查报告草稿',
                    tool_name='draft_report + review_report + finalize_report',
                    required_inputs=['analysis_result', 'risk_list', 'evidence_pack'],
                    candidate_tools=['draft_report', 'review_report', 'finalize_report'],
                    produced_artifacts=['report_draft', 'review_result', 'final_report'],
                    failure_branch='fallback',
                    success_condition='报告字段完整且主要结论具备证据支撑',
                ),
            ],
            exit_criteria=[
                '报告字段完整',
                '关键结论有证据支撑',
                f'覆盖重点维度：{"、".join(focus_aspects[:4])}',
            ],
        )

    def replan_for_evidence_gap(
        self,
        request: TaskRequest,
        current_plan: TaskPlan | None,
        missing_aspects: list[str],
    ) -> TaskPlan:
        """在证据覆盖不足时进行局部重规划。

        Args:
            request: 当前任务请求对象。
            current_plan: 当前已存在的任务计划；为空时回退到首版计划。
            missing_aspects: 当前证据尚未覆盖的维度列表。

        Returns:
            插入补证据步骤后的新计划。
        """

        base_plan = current_plan or self.plan(request)
        extra_step = TaskStep(
            step_id='s2r',
            intent='记录证据缺口并在分析结论中显式披露',
            tool_name='rag_retrieve_graph_evidence + analyze',
            required_inputs=['task_request.instructions', 'evidence_pack'],
            candidate_tools=['rag_retrieve_graph_evidence', 'extract_key_points', 'extract_risks'],
            produced_artifacts=['evidence_gap_note', 'analysis_result'],
            failure_branch='skip_with_gap',
            success_condition='保留已获证据并明确标注缺失维度',
        )
        steps = self._bounded_steps(base_plan.steps, extra_step, base_plan.max_steps)
        exit_criteria = list(base_plan.exit_criteria)
        if missing_aspects:
            exit_criteria.append(f'显式披露证据缺口：{"、".join(missing_aspects[:4])}')
        return TaskPlan(
            goal=base_plan.goal,
            expected_artifact=base_plan.expected_artifact,
            max_steps=base_plan.max_steps,
            steps=steps,
            exit_criteria=self._dedupe(exit_criteria),
        )

    def replan_for_review(
        self,
        request: TaskRequest,
        current_plan: TaskPlan | None,
        missing_sections: list[str],
        unsupported_claims: list[str],
    ) -> TaskPlan:
        """在报告审查未通过时追加一次受控修订计划。

        Args:
            request: 当前任务请求对象。
            current_plan: 当前已存在的任务计划；为空时回退到首版计划。
            missing_sections: 审查发现缺失的报告字段。
            unsupported_claims: 当前仍缺乏证据支撑的结论列表。

        Returns:
            插入修订步骤后的新计划。
        """

        base_plan = current_plan or self.plan(request)
        extra_step = TaskStep(
            step_id='s4r',
            intent='根据审查结果修订报告并再次审查',
            tool_name='draft_report + review_report',
            required_inputs=['report_draft', 'review_result'],
            candidate_tools=['draft_report', 'review_report'],
            produced_artifacts=['report_draft', 'review_result'],
            failure_branch='fallback',
            success_condition='补齐缺失字段并消除 unsupported claims',
        )
        steps = self._bounded_steps(base_plan.steps, extra_step, base_plan.max_steps)
        exit_criteria = list(base_plan.exit_criteria)
        if missing_sections:
            exit_criteria.append(f'补齐缺失字段：{"、".join(missing_sections[:4])}')
        if unsupported_claims:
            exit_criteria.append('unsupported claims 为 0')
        return TaskPlan(
            goal=base_plan.goal,
            expected_artifact=base_plan.expected_artifact,
            max_steps=base_plan.max_steps,
            steps=steps,
            exit_criteria=self._dedupe(exit_criteria),
        )

    def _bounded_steps(self, existing_steps: list[TaskStep], extra_step: TaskStep, max_steps: int) -> list[TaskStep]:
        """在不突破计划上限的前提下追加一步。

        Args:
            existing_steps: 当前计划中的步骤列表。
            extra_step: 待追加的补充步骤。
            max_steps: 计划允许的最大步骤数。

        Returns:
            处理后的步骤列表；若已存在或超限则保持原样。
        """
        step_ids = {step.step_id for step in existing_steps}
        if extra_step.step_id in step_ids:
            return list(existing_steps)
        if len(existing_steps) >= max_steps:
            return list(existing_steps)
        return [*existing_steps, extra_step]

    def _dedupe(self, items: list[str]) -> list[str]:
        """按原顺序去重字符串列表。

        Args:
            items: 待去重的文本列表。

        Returns:
            去重并过滤空值后的结果列表。
        """
        results: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            results.append(normalized)
            seen.add(normalized)
        return results

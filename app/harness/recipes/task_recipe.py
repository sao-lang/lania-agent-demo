"""Task 工作流的 Recipe 定义。

以声明式 Recipe + Stage 方式组织文档分析等任务的执行流程。
Phase 4：各 Stage 填入真实业务逻辑，通过 ctx 访问运行时依赖。
"""

from __future__ import annotations

from typing import Any

from app.harness.core.recipe import BaseRecipe
from app.harness.core.stage import BaseStage


class PlanStage(BaseStage):
    """任务计划阶段。

    根据任务请求生成执行计划，使用 planner 组件。
    """

    def __init__(self) -> None:
        super().__init__(
            name='plan',
            description='根据任务请求生成执行计划',
        )

    def run(self, state: dict, ctx) -> dict:
        planner = ctx.get('planner')
        task = state.get('task')

        if planner and task:
            try:
                plan = planner.generate_plan(task)
                state['plan'] = plan.model_dump() if hasattr(plan, 'model_dump') else plan
            except Exception:
                state['plan_error'] = True

        return state


class CollectDocumentContextStage(BaseStage):
    """文档上下文收集阶段。

    加载文档上下文信息，通过 execution_harness 调用 rag_load_document_context。
    """

    def __init__(self) -> None:
        super().__init__(
            name='collect_document_context',
            description='加载文档上下文信息',
            allowed_tools=['rag_load_document_context'],
            requires_policy_check=False,
        )

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        request = state.get('request', {})

        if execution and hasattr(execution, 'run_tool'):
            try:
                result = execution.run_tool(
                    name='rag_load_document_context',
                    payload={
                        'collection_name': request.get('collection_name', ''),
                        'doc_ids': request.get('doc_ids', []),
                    },
                    workflow_state=state,
                    context_bundle=None,
                )
                state['document_context'] = result.model_dump() if hasattr(result, 'model_dump') else result
            except Exception:
                state['context_error'] = True

        return state


class RetrieveEvidenceStage(BaseStage):
    """证据检索阶段。

    执行混合及图谱检索，收集证据。
    通过 execution_harness 调用 rag_retrieve_evidence。
    """

    def __init__(self) -> None:
        super().__init__(
            name='retrieve_evidence',
            description='执行混合及图谱检索，收集证据',
            allowed_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
        )

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        context_harness = ctx.get('context_harness')
        request = state.get('request', {})

        if context_harness and hasattr(context_harness, 'build_context'):
            try:
                bundle = context_harness.build_context(
                    workflow_state=state,
                    step_id=self.name,
                )
                state['context_bundle'] = bundle.model_dump() if hasattr(bundle, 'model_dump') else bundle
            except Exception:
                state['context_bundle_error'] = True

        if execution and hasattr(execution, 'run_tool'):
            try:
                result = execution.run_tool(
                    name='rag_retrieve_evidence',
                    payload={
                        'query': request.get('objective', ''),
                        'collection_name': request.get('collection_name', ''),
                    },
                    workflow_state=state,
                    context_bundle=None,
                )
                state['evidence'] = result.model_dump() if hasattr(result, 'model_dump') else result
            except Exception:
                state['evidence_error'] = True

        return state


class AnalyzeStage(BaseStage):
    """分析阶段。

    基于检索到的证据执行结构化分析。
    使用 prompt_builder 和 llm 生成分析内容。
    """

    def __init__(self) -> None:
        super().__init__(
            name='analyze',
            description='基于检索到的证据执行结构化分析',
            creates_checkpoint_after=True,
        )

    def run(self, state: dict, ctx) -> dict:
        prompt_builder = ctx.get('prompt_builder')
        grounding = ctx.get('grounding_engine')
        llm = ctx.get('llm')
        evidence = state.get('evidence')

        if prompt_builder and evidence:
            try:
                prompt = prompt_builder.render('analyze', {'evidence': evidence})
                if llm and prompt:
                    prompt_text = prompt.text if hasattr(prompt, 'text') else str(prompt)
                    state['analysis'] = llm.complete(prompt_text).text
                    if grounding and hasattr(grounding, 'align_claims'):
                        grounding_result = grounding.align_claims(state['analysis'], evidence)
                        state['grounding'] = (
                            grounding_result.model_dump()
                            if hasattr(grounding_result, 'model_dump')
                            else grounding_result
                        )
            except Exception:
                state['analysis_error'] = True

        return state


class DraftReportStage(BaseStage):
    """报告草稿生成阶段。

    基于分析结果生成报告草稿。
    通过 execution_harness 调用 draft_report 工具。
    """

    def __init__(self) -> None:
        super().__init__(
            name='draft_report',
            description='基于分析结果生成报告草稿',
            allowed_tools=['draft_report'],
            risk_level='medium',
        )

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')

        if execution and hasattr(execution, 'run_tool') and state.get('analysis'):
            try:
                result = execution.run_tool(
                    name='draft_report',
                    payload={
                        'analysis': state['analysis'],
                        'evidence': state.get('evidence'),
                    },
                    workflow_state=state,
                    context_bundle=None,
                )
                state['draft'] = result.model_dump() if hasattr(result, 'model_dump') else result
            except Exception:
                state['draft_error'] = True

        return state


class ReviewStage(BaseStage):
    """审查阶段。

    对报告草稿做质量审查，决定是否通过。
    通过 execution_harness 调用 review_report 工具。
    条件路由：未通过 → revise，通过 → finalize_report。
    """

    def __init__(self) -> None:
        super().__init__(
            name='review',
            description='对报告草稿做质量审查',
            allowed_tools=['review_report'],
            route_targets=['revise', 'finalize_report'],
        )

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')

        if execution and hasattr(execution, 'run_tool') and state.get('draft'):
            try:
                result = execution.run_tool(
                    name='review_report',
                    payload={'draft': state['draft']},
                    workflow_state=state,
                    context_bundle=None,
                )
                review = result.model_dump() if hasattr(result, 'model_dump') else result
                state['review'] = review
                state['review_passed'] = review.get('passed', False) if isinstance(review, dict) else getattr(result, 'passed', False)
            except Exception:
                state['review_error'] = True
                state['review_passed'] = False

        return state

    def route_next(self, state_payload: dict) -> str:
        if not state_payload.get('review_passed', True):
            return 'revise'
        return 'finalize_report'


class ReviseStage(BaseStage):
    """修订阶段。

    根据审查意见修订报告草稿。
    通过 execution_harness 调用 draft_report 工具，传入 review_feedback。
    """

    def __init__(self) -> None:
        super().__init__(
            name='revise',
            description='根据审查意见修订报告',
            allowed_tools=['draft_report'],
        )

    def run(self, state: dict, ctx) -> dict:
        if not state.get('review_passed', True):
            execution = ctx.get('execution_harness')

            if execution and hasattr(execution, 'run_tool') and state.get('draft') and state.get('review'):
                try:
                    result = execution.run_tool(
                        name='draft_report',
                        payload={
                            'draft': state['draft'],
                            'review_feedback': state['review'],
                        },
                        workflow_state=state,
                        context_bundle=None,
                    )
                    state['draft'] = result.model_dump() if hasattr(result, 'model_dump') else result
                    state['revised'] = True
                except Exception:
                    state['revise_error'] = True

        return state


class FinalizeReportStage(BaseStage):
    """报告最终确认阶段。

    最终确认并输出报告。
    通过 execution_harness 调用 finalize_report 工具。
    """

    def __init__(self) -> None:
        super().__init__(
            name='finalize_report',
            description='最终确认并输出报告',
            allowed_tools=['finalize_report'],
            risk_level='high',
            creates_checkpoint_after=True,
        )

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')

        if execution and hasattr(execution, 'run_tool') and state.get('draft'):
            try:
                result = execution.run_tool(
                    name='finalize_report',
                    payload={'draft': state['draft']},
                    workflow_state=state,
                    context_bundle=None,
                )
                state['final_artifact'] = result.model_dump() if hasattr(result, 'model_dump') else result
            except Exception:
                state['finalize_error'] = True

        return state


class DocumentAnalysisRecipe(BaseRecipe):
    """文档分析任务 recipe。

    plan → collect_document_context → retrieve_evidence → analyze → draft_report → review → revise → finalize_report
    其中 review 可能路由到 revise 做修订，修订后回到 review 重新审查。
    """

    name = 'document_analysis'
    description = '文档分析工作流：计划 → 上下文 → 检索 → 分析 → 起草 → 审查 → 修订 → 确认'
    task_type = 'document_analysis'
    version = 'v1'

    def __init__(self) -> None:
        super().__init__(stages=[
            PlanStage(),
            CollectDocumentContextStage(),
            RetrieveEvidenceStage(),
            AnalyzeStage(),
            DraftReportStage(),
            ReviewStage(),
            ReviseStage(),
            FinalizeReportStage(),
        ])


class DocumentSummaryRecipe(BaseRecipe):
    """文档摘要任务 recipe。

    collect_document_context → analyze → finalize_report
    """

    name = 'document_summary'
    description = '文档摘要工作流：上下文 → 分析 → 输出'
    task_type = 'document_summary'
    version = 'v1'

    def __init__(self) -> None:
        super().__init__(stages=[
            CollectDocumentContextStage(),
            AnalyzeStage(),
            FinalizeReportStage(),
        ])
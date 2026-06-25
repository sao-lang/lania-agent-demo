"""ContextHarness phase3 构建器模块。

拆分 task/query 两类上下文装配职责，负责把工作流状态裁剪为步骤级
``ContextBundle``，并附带预算、压缩和 grounding 等优化信息。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.agents.memory import TaskMemory
from app.core.config import Settings
from app.harness.budgeting import TokenBudgetEngine
from app.harness.compression import CompressionEngine
from app.harness.components.context_models import ContextOptimizationResult
from app.harness.context_policy import ContextPolicy, ContextSourceType
from app.harness.grounding import GroundingEngine, GroundingResult
from app.harness.models import ContextBundle
from app.harness.selection import SelectionEngine
from app.models.artifact import ReportArtifactContent
from app.models.task import StepSpec, TaskDetail, TaskPlan


class ContextValueSerializer:
    """把运行时对象递归转换为 JSON 友好的结构。"""

    def jsonable(self, value: Any) -> Any:
        """递归序列化 BaseModel、列表和字典。"""

        if isinstance(value, BaseModel):
            return value.model_dump(mode='json')
        if isinstance(value, list):
            return [self.jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self.jsonable(item) for key, item in value.items()}
        return value


class TaskContextBuilder:
    """构建任务/文档分析场景的上下文包。"""

    def __init__(self, memory: TaskMemory, settings: Settings, serializer: ContextValueSerializer | None = None) -> None:
        """初始化任务上下文构建所需的选择、压缩、预算与 grounding 组件。"""

        self.memory = memory
        self.settings = settings
        self.serializer = serializer or ContextValueSerializer()
        self.selection_engine = SelectionEngine()
        self.compression_engine = CompressionEngine()
        self.budget_engine = TokenBudgetEngine()
        self.grounding_engine = GroundingEngine()

    def build_context(self, workflow_state: dict[str, Any], step_id: str | None = None) -> ContextBundle:
        """构建简化版上下文包，仅返回最终上下文结果。"""

        return self.build_optimized_context(workflow_state, step_id).context_bundle

    def build_optimized_context(self, workflow_state: dict[str, Any], step_id: str | None = None) -> ContextOptimizationResult:
        """构建带预算与压缩信息的完整上下文优化结果。"""

        task = TaskDetail.model_validate(workflow_state['task'])
        plan = task.plan
        resolved_step_id = step_id or str(workflow_state.get('active_plan_step_id') or task.current_step or 'unknown')
        step = self._resolve_plan_step(plan, resolved_step_id)
        step_spec = self._resolve_step_spec(task, resolved_step_id)

        policy = ContextPolicy.for_step(resolved_step_id)
        objective = step.intent if step is not None else (step_spec.objective if step_spec is not None else task.request.instructions)

        evidence_pack = workflow_state.get('evidence_pack')
        analysis = workflow_state.get('analysis') or {}
        draft_content = workflow_state.get('draft_content')

        self.budget_engine.allocate_budget(policy)

        state_slice = self.selection_engine.select_state(workflow_state, policy)
        self.budget_engine.record_usage(ContextSourceType.STATE, state_slice)

        evidence_items = []
        coverage_score = 0.0
        missing_aspects: list[str] = []
        if evidence_pack is not None:
            raw_evidence = [self.serializer.jsonable(item) for item in evidence_pack.evidence_items]
            selected_evidence = self.selection_engine.select_evidence(raw_evidence, policy, objective)
            compressed_evidence = self.compression_engine.compress_evidence(selected_evidence, policy)
            evidence_items = compressed_evidence[:policy.evidence_top_k]
            coverage_score = float(evidence_pack.coverage_score)
            missing_aspects = list(evidence_pack.missing_aspects[:5])
        self.budget_engine.record_usage(ContextSourceType.EVIDENCE, evidence_items)

        recent_reflections = [
            {
                'step': item.step,
                'trigger': item.trigger,
                'decision': item.decision,
                'summary': item.summary,
            }
            for item in task.reflection_entries[-policy.reflection_limit:]
        ]

        recent_artifact_memory = [
            {
                'artifact_id': item.artifact_id,
                'version': item.version,
                'status': item.status,
                'summary': item.summary,
            }
            for item in task.artifact_memory_entries[-policy.artifact_memory_limit:]
        ]

        memory_data = {
            'task_memory': self.selection_engine.select_memory(task.task_memory_entries, policy, objective)['task_memory'],
            'reflections': recent_reflections,
            'artifact_memory': recent_artifact_memory,
            'coverage_score': coverage_score,
            'missing_aspects': missing_aspects,
            'plan_version': task.plan_version,
            'plan_goal': plan.goal if plan is not None else None,
            'exit_criteria_failures': list(workflow_state.get('exit_criteria_failures') or []),
        }
        compressed_memory = self.compression_engine.compress_memory(memory_data['task_memory'], policy)
        memory_data['task_memory'] = compressed_memory
        self.budget_engine.record_usage(ContextSourceType.MEMORY, memory_data)

        artifact_slice = self.selection_engine.select_artifact(draft_content, policy)
        artifact_slice = self.compression_engine.compress_artifact(artifact_slice, policy)
        self.budget_engine.record_usage(ContextSourceType.ARTIFACT, artifact_slice)

        tool_options = list(step.candidate_tools if step is not None else [])
        if not tool_options and step_spec is not None:
            tool_options = list(step_spec.allowed_tools)
        if not tool_options:
            tool_options = self._default_tool_options(resolved_step_id)

        context_data = {
            'state_slice': state_slice,
            'evidence_slice': evidence_items,
            'artifact_slice': artifact_slice,
            'memory_slice': memory_data,
        }

        enforced_context = self.budget_engine.enforce_budget(context_data, policy)

        compression_ratio = self.compression_engine.calculate_compression_ratio(
            evidence_items, enforced_context['evidence_slice']
        )

        budget_status = self.budget_engine.get_budget_status()
        grounding_result = self._build_grounding_bundle(evidence_pack, analysis, draft_content)

        context_bundle = ContextBundle(
            step_id=resolved_step_id,
            objective=objective,
            state_slice=enforced_context['state_slice'],
            evidence_slice=enforced_context['evidence_slice'],
            artifact_slice=enforced_context['artifact_slice'],
            memory_slice=enforced_context['memory_slice'],
            tool_options=tool_options,
            token_budget=self.budget_engine.get_total_budget(),
        )

        optimization_info = {
            'step_id': resolved_step_id,
            'policy_name': policy.step_type,
            'evidence_count': len(evidence_items),
            'memory_count': len(memory_data['task_memory']),
            'compression_applied': policy.compression_enabled,
            'alignment_score': grounding_result.alignment_score if grounding_result else 0.0,
            'coverage_ratio': grounding_result.coverage_ratio if grounding_result else 0.0,
            'unsupported_claim_count': grounding_result.unsupported_claim_count if grounding_result else 0,
        }

        return ContextOptimizationResult(
            context_bundle=context_bundle,
            policy=policy,
            budget_status=budget_status,
            compression_ratio=compression_ratio,
            saved_tokens=self.budget_engine.get_total_budget() - self.budget_engine.get_total_usage(),
            optimization_info=optimization_info,
            grounding_result=grounding_result,
        )

    def _build_grounding_bundle(
        self,
        evidence_pack,
        analysis: dict[str, Any] | None = None,
        draft_content: ReportArtifactContent | None = None,
    ) -> GroundingResult | None:
        """在具备证据时构造 grounding 评估结果。"""

        if evidence_pack is None:
            return None
        return self.grounding_engine.build_grounding_bundle(evidence_pack, analysis, draft_content)

    def _resolve_plan_step(self, plan: TaskPlan | None, step_id: str):
        """按步骤 ID 在计划中查找对应步骤。"""

        if plan is None:
            return None
        for step in plan.steps:
            if step.step_id == step_id:
                return step
        return None

    def _resolve_step_spec(self, task: TaskDetail, step_id: str) -> StepSpec | None:
        """按步骤 ID 在任务规范中查找步骤定义。"""

        if task.task_spec is None:
            return None
        for step in task.task_spec.steps:
            if step.step_id == step_id:
                return step
        return None

    def _default_tool_options(self, step_id: str) -> list[str]:
        """在缺少显式候选工具时提供默认工具路由。"""

        default_routes = {
            'collect_document_context': ['rag_load_document_context'],
            'retrieve_evidence': ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
            'handle_evidence_gap': ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
            'analyze': ['extract_key_points', 'extract_risks'],
            'draft_artifact': ['draft_report'],
            'review_artifact': ['review_report'],
            'revise_artifact': ['review_report', 'draft_report'],
            'finalize': ['finalize_report'],
        }
        return default_routes.get(step_id, [])


class QueryContextBuilder:
    """构建 query/chat runtime 使用的上下文包。"""

    def __init__(self, serializer: ContextValueSerializer | None = None) -> None:
        """初始化 query 上下文构建器。"""

        self.serializer = serializer or ContextValueSerializer()

    def build_query_context(self, workflow_state: dict[str, Any], step_spec: StepSpec) -> ContextBundle:
        """根据 query runtime 状态生成步骤级上下文包。"""

        request = workflow_state['request']
        citations = workflow_state.get('citations') or []
        reflection_decision = workflow_state.get('reflection_decision')
        corrective_info = workflow_state.get('corrective_info') or {}
        state_slice = {
            'collection_name': request.collection_name,
            'question': request.question.strip(),
            'session_id': request.session_id,
            'guardrail_state': self.serializer.jsonable(workflow_state.get('guardrail_state') or {}),
            'retrieval_questions': list(workflow_state.get('retrieval_questions') or []),
            'cache_info': self.serializer.jsonable(workflow_state.get('cache_info') or {}),
            'cache_hit': bool(workflow_state.get('cache_hit')),
            'contexts': list(workflow_state.get('contexts') or []),
            'answer_mode': workflow_state.get('answer_mode'),
            'raw_answer_mode': workflow_state.get('raw_answer_mode'),
        }
        memory_slice = {
            'completed_step_ids': list(workflow_state.get('completed_step_ids') or []),
            'retry_count': int(workflow_state.get('retry_count') or 0),
            'reflection_decision': reflection_decision.model_dump(mode='json') if reflection_decision is not None else None,
            'missing_aspects': list(corrective_info.get('missing_aspects') or []),
            'risk': corrective_info.get('risk'),
        }
        artifact_slice: dict[str, Any] | None = None
        if workflow_state.get('answer') or workflow_state.get('raw_answer'):
            artifact_slice = {
                'raw_answer': workflow_state.get('raw_answer'),
                'answer': workflow_state.get('answer'),
                'answer_mode': workflow_state.get('answer_mode'),
            }
        return ContextBundle(
            step_id=step_spec.step_id,
            objective=step_spec.objective,
            state_slice=state_slice,
            evidence_slice=[self.serializer.jsonable(item) for item in citations],
            artifact_slice=artifact_slice,
            memory_slice=memory_slice,
            tool_options=list(step_spec.allowed_tools),
            token_budget=max(1, int(getattr(request, 'top_k', 0) or 0) * 512),
        )

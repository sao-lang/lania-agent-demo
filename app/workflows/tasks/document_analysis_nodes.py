"""文档分析任务工作流节点模块。

负责把文档分析任务拆分为计划生成、上下文加载、证据检索、分析、草稿生成、审查、
修订和最终交付等节点，并在节点之间维护计划状态、回退分支、子 Agent 协作和任务记忆。
"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Optional, cast
from uuid import uuid4

from app.agents.memory import TaskMemory
from app.agents.planner import TaskPlanner
from app.agents.artifacts import ArtifactFormatter
from app.agents.subagents import (
    DraftArtifactInput,
    DraftArtifactResult,
    EvidenceCollectionInput,
    EvidenceCollectionResult,
    EvidenceSupplementInput,
    EvidenceSupplementResult,
    ReviewDraftInput,
    ReviewDraftResult,
    ReviseDraftInput,
    ReviseDraftResult,
    SubAgentHandoff,
    SubAgentRuntime,
)
from app.agents.tools.base import ToolExecutionError
from app.agents.tools.registry import ToolRegistry
from app.harness.context import ContextHarness
from app.harness.evaluation import EvaluationHarness
from app.harness.execution import ExecutionHarness
from app.harness.grounding import GroundingEngine, GroundingResult
from app.harness.guardrails import GuardrailEngine
from app.harness.prompting import PromptBuilder, PromptRenderResult
from app.harness.models import ContextBundle
from app.harness.policy import PolicyEngine
from app.capabilities.knowledge import DocumentContextItem, DocumentContextResult
from app.models.artifact import EvidencePack, ReportArtifactContent, ReviewResult
from app.models.task import CheckpointRecord, StepRuntimeRecord, TaskDetail, TaskResult, TaskRunEvent
from app.rag.observability import TraceRecorder
from app.runtime_contract_adapters import (
    build_prompt_build_request,
    build_retrieval_quality_report,
    evidence_pack_to_graph_subgraph,
    evidence_pack_to_grounded_context,
    prompt_render_result_to_build_result,
    prompt_template_to_spec,
)
from app.workflows.step_lifecycle import create_checkpoint, create_run_event, mark_step_completed, mark_step_failed, mark_step_started
from app.workflows.tasks.document_analysis_state import DocumentAnalysisState, DocumentAnalysisUpdate


class DocumentAnalysisNodes:
    """承载基于计划驱动的文档分析任务工作流节点。

    该类把结构化文档任务拆成“载入任务、计划、证据收集、分析、起草、审查、修订、退出评估、
    最终交付”这条主链，并统一维护计划步骤队列、任务记忆、子 Agent 协作、降级回退、
    checkpoint 与运行事件。
    """

    def __init__(
        self,
        planner: TaskPlanner,
        registry: ToolRegistry,
        memory: TaskMemory,
        trace: TraceRecorder,
        settings,
        state,
        retrieval,
        vector_store,
        llm,
        subagent_runtime: SubAgentRuntime,
        context_harness: ContextHarness,
        execution_harness: ExecutionHarness,
        guardrail_engine: GuardrailEngine,
        policy_engine: PolicyEngine,
        evaluation_harness: EvaluationHarness,
        grounding_engine: GroundingEngine | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        """初始化文档分析任务节点集合。

        Args:
            planner: 任务计划生成器。
            registry: 工具注册表。
            memory: 任务内存与持久化访问封装。
            trace: 链路追踪记录器。
            settings: 全局配置对象。
            state: 内存态业务数据。
            retrieval: 检索服务实例。
            vector_store: 向量库访问封装。
            llm: 可选大模型实例。
            subagent_runtime: 子 Agent 运行时。
            grounding_engine: Grounding 引擎实例，用于建立证据与结论的显式绑定。
            prompt_builder: PromptBuilder 实例，用于统一管理和渲染提示词模板。
        """
        self.planner = planner
        self.registry = registry
        self.memory = memory
        self.trace = trace
        self.settings = settings
        self.state = state
        self.retrieval = retrieval
        self.vector_store = vector_store
        self.llm = llm
        self.subagent_runtime = subagent_runtime
        self.context_harness = context_harness
        self.execution_harness = execution_harness
        self.guardrail_engine = guardrail_engine
        self.policy_engine = policy_engine
        self.evaluation_harness = evaluation_harness
        self.grounding_engine = grounding_engine or GroundingEngine()
        self.prompt_builder = prompt_builder or PromptBuilder()

    def dispatch_plan_step(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """刷新任务状态并准备进入下一计划步骤。

        该节点本身不执行业务步骤，只负责把最新任务快照和计划状态同步回工作流，确保后续路由
        决策基于最新持久化状态而不是旧快照。
        """
        task = self._refresh_task(workflow_state['task'])
        return {
            'task': task,
            'plan': task.plan,
            'exit_decision': None,
        }

    def route_plan_step(self, workflow_state: DocumentAnalysisState) -> str:
        """根据待执行计划步骤选择下一个节点。

        当前实现把 planner 产出的抽象步骤编号映射到具体 workflow 节点，从而让计划层可以保持
        简洁的步骤语义，而图编排层继续使用明确的 handler 名称。
        """
        step_id = self._peek_pending_plan_step(workflow_state)
        step_routes = {
            's1': 'collect_document_context',
            's2': 'retrieve_evidence',
            's2r': 'handle_evidence_gap',
            's3': 'analyze',
            's4': 'draft_artifact',
            's4r': 'revise_artifact',
        }
        if step_id is None:
            return 'evaluate_exit_criteria'
        return step_routes.get(step_id, 'evaluate_exit_criteria')

    def route_after_review(self, workflow_state: DocumentAnalysisState) -> str:
        """在审查节点后决定回到计划分发还是退出判断。"""
        if self._peek_pending_plan_step(workflow_state) is not None:
            return 'dispatch_plan_step'
        return 'evaluate_exit_criteria'

    def route_exit_decision(self, workflow_state: DocumentAnalysisState) -> str:
        """根据退出条件判断进入 `finalize` 还是其他分支。"""
        return str(workflow_state.get('exit_decision') or 'finalize')

    def load_task(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """加载任务并初始化执行状态。

        这是任务工作流的入口步骤，主要负责把请求级元信息写入记忆与运行态，作为后续计划生成、
        证据检索和报告生成的基础上下文。
        """
        task = self._enter_step(workflow_state['task'], 'load_task')
        self.memory.append_task_memory(
            task.task_id,
            'load_task',
            'state',
            '任务已加载，开始初始化执行状态。',
            payload={
                'task_type': task.request.task_type,
                'collection_name': task.request.collection_name,
                'doc_count': len(task.request.doc_ids),
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'load_task')
        return {'task': task}

    def plan_task(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """生成首版执行计划并初始化计划相关状态。

        该步骤会依次经过 task policy、plan guardrail 和 plan policy 三层校验，确保后续图执行的
        计划既满足业务目标，也满足策略和安全约束。
        """
        task = self._enter_step(workflow_state['task'], 'plan_task')
        task_policy_decision = self.policy_engine.check_task(task.request)
        self._raise_policy_error(task_policy_decision)
        plan = self.planner.plan(task.request)
        plan_decision = self.guardrail_engine.validate_plan(plan)
        self.guardrail_engine.raise_plan_error(plan_decision)
        plan_policy_decision = self.policy_engine.check_plan(task.request, plan)
        self._raise_policy_error(plan_policy_decision)
        task.plan = plan
        task.plan_version = max(1, task.plan_version)
        task.focus_aspects = self.planner.derive_focus_aspects(task.request.instructions)
        self.memory.upsert_task(task)
        self.memory.append_task_memory(
            task.task_id,
            'plan_task',
            'state',
            '生成首版有界执行计划。',
            payload={
                'plan_version': task.plan_version,
                'step_count': len(plan.steps),
                'focus_aspects': task.focus_aspects,
                'exit_criteria': plan.exit_criteria,
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'plan_task')
        return {
            'task': task,
            'plan': plan,
            'focus_aspects': task.focus_aspects,
            'pending_plan_step_ids': [step.step_id for step in plan.steps],
            'completed_plan_step_ids': [],
            'active_plan_step_id': None,
            'exit_criteria_failures': [],
            'exit_decision': None,
        }

    def collect_document_context(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """加载任务关联文档的基础上下文。

        这里优先走正式工具链获取文档上下文；若工具失败，则可回退到基于内存态文档元数据的降级
        结果，保证后续计划至少能在有限上下文下继续执行。
        """
        pending_plan_step_ids, active_plan_step_id = self._activate_plan_step(workflow_state, 's1')
        task = self._enter_step(workflow_state['task'], 'collect_document_context')
        context_bundle = self.context_harness.build_context(workflow_state, 'collect_document_context')
        output = self._run_tool(
            'rag_load_document_context',
            {'collection_name': task.request.collection_name, 'doc_ids': task.request.doc_ids},
            workflow_state,
            context_bundle=context_bundle,
            fallback_factory=lambda exc: self._build_document_context_fallback(task, exc),
        )
        task = self._refresh_task(task)
        if not output.documents:
            raise RuntimeError('no documents available for task analysis')
        self.memory.append_task_memory(
            task.task_id,
            'collect_document_context',
            'context',
            f'加载 {len(output.documents)} 篇文档上下文。',
            payload={
                'documents': [
                    {'doc_id': item.doc_id, 'title': item.title, 'sections': item.sections[:5]}
                    for item in output.documents
                ]
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'collect_document_context')
        completed_plan_step_ids, active_plan_step_id = self._complete_plan_step(workflow_state, 's1')
        return {
            'task': task,
            'document_context': output.model_dump(mode='json'),
            'tool_call_count': int(workflow_state.get('tool_call_count') or 0) + 1,
            'pending_plan_step_ids': pending_plan_step_ids,
            'completed_plan_step_ids': completed_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def retrieve_evidence(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """通过证据子 Agent 检索并合并证据包。

        该步骤是任务主链里最重要的 grounding 环节之一。它会调用证据子 Agent、多种检索工具和
        失败分支策略，并把多轮结果合并成后续分析、起草和审查可复用的统一 `EvidencePack`。
        """
        pending_plan_step_ids, active_plan_step_id = self._activate_plan_step(workflow_state, 's2')
        task = self._enter_step(workflow_state['task'], 'retrieve_evidence')
        focus_aspects = list(workflow_state.get('focus_aspects') or task.focus_aspects)
        evidence_result = cast(
            EvidenceCollectionResult,
            self.subagent_runtime.execute(
            'evidence_agent',
            'collect',
            EvidenceCollectionInput(
                task_id=task.task_id,
                query=task.request.instructions,
                collection_name=task.request.collection_name,
                doc_ids=task.request.doc_ids,
                top_k=task.request.constraints.top_k,
                focus_aspects=focus_aspects,
            ),
            handoff=self._build_subagent_handoff(
                source_step_id='retrieve_evidence',
                context_keys=['task.request.instructions', 'task.request.doc_ids', 'focus_aspects'],
                sandbox_profile='restricted',
            ),
            runner=lambda tool_name, payload: self._run_tool(
                tool_name,
                payload,
                workflow_state,
                fallback_factory=lambda exc: self._build_evidence_gap_fallback(
                    task,
                    list(payload.get('focus_aspects') or focus_aspects),
                    exc,
                ),
            ),
            merge_packs=self._merge_evidence_packs,
            ),
        )
        task = self._refresh_task(task)
        tool_calls = int(workflow_state.get('tool_call_count') or 0) + len(evidence_result.selected_tools)
        merged_pack = evidence_result.evidence_pack
        task.evidence_pack_id = f'ev-{task.task_id}'
        task.grounded_context = evidence_pack_to_grounded_context(
            objective=task.request.instructions,
            evidence_pack=merged_pack,
            evidence_pack_ref=task.evidence_pack_id,
        )
        task.graph_subgraph = evidence_pack_to_graph_subgraph(merged_pack)
        task.retrieval_quality_report = build_retrieval_quality_report(
            query=task.request.instructions,
            coverage_score=float(merged_pack.coverage_score),
            relevance_score=min(1.0, len(merged_pack.evidence_items) / max(1, task.request.constraints.top_k)),
            confidence_score=max(0.0, 1.0 - (0.15 * len(merged_pack.missing_aspects))),
            suggested_actions=['补充 focus_aspects'] if merged_pack.missing_aspects else [],
        )
        self.memory.upsert_task(task)
        self.memory.append_task_memory(
            task.task_id,
            'retrieve_evidence',
            'evidence',
            f'检索得到 {len(merged_pack.evidence_items)} 条证据，覆盖度 {merged_pack.coverage_score:.2f}。',
            payload={
                'coverage_score': merged_pack.coverage_score,
                'missing_aspects': merged_pack.missing_aspects,
                'evidence_count': len(merged_pack.evidence_items),
            },
        )
        # 当证据覆盖不足时，只插入一次局部重规划步骤，而不是整条任务重新开始。
        if evidence_result.decision == 'replan' and merged_pack.missing_aspects:
            updated_plan = self.planner.replan_for_evidence_gap(task.request, task.plan, merged_pack.missing_aspects)
            task.plan = updated_plan
            task = self.memory.append_plan_revision(
                task.task_id,
                trigger='evidence_gap',
                reason=f'证据覆盖不足：{"、".join(merged_pack.missing_aspects[:4])}',
                added_steps=['s2r'],
                plan=updated_plan,
            ) or task
            self.memory.append_task_memory(
                task.task_id,
                'retrieve_evidence',
                'replan',
                '证据覆盖存在缺口，已触发局部重规划。',
                payload={
                    'plan_version': task.plan_version,
                    'missing_aspects': merged_pack.missing_aspects,
                },
            )
            task = self.memory.append_reflection(
                task.task_id,
                step='retrieve_evidence',
                trigger='evidence_gap',
                decision='replan',
                summary='证据覆盖存在缺口，已触发一次局部重规划。',
                missing_aspects=merged_pack.missing_aspects,
                plan_version=task.plan_version,
            ) or task
            self.trace.record(
                'task_replanned',
                {
                    'task_id': task.task_id,
                    'trigger': 'evidence_gap',
                    'plan_version': task.plan_version,
                    'missing_aspects': merged_pack.missing_aspects,
                },
            )
            pending_plan_step_ids = self._prepend_pending_plan_step(
                pending_plan_step_ids,
                workflow_state,
                's2r',
            )
        else:
            task = self.memory.append_reflection(
                task.task_id,
                step='retrieve_evidence',
                trigger='evidence_gap',
                decision='continue',
                summary='证据覆盖满足当前分析要求，继续进入分析步骤。',
                plan_version=task.plan_version,
            ) or task
        task = self._refresh_task(task)
        self._mark_progress(task, 'retrieve_evidence')
        completed_plan_step_ids, active_plan_step_id = self._complete_plan_step(workflow_state, 's2')
        return {
            'task': task,
            'evidence_pack': merged_pack,
            'tool_call_count': tool_calls,
            'plan': task.plan,
            'pending_plan_step_ids': pending_plan_step_ids,
            'completed_plan_step_ids': completed_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def handle_evidence_gap(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """针对证据缺口执行一次补证据回路。"""
        pending_plan_step_ids, active_plan_step_id = self._activate_plan_step(workflow_state, 's2r')
        task = self._enter_step(workflow_state['task'], 'handle_evidence_gap')
        evidence_pack = workflow_state.get('evidence_pack')
        if evidence_pack is None:
            raise RuntimeError('evidence_pack is required before handle_evidence_gap')
        missing_aspects = list(evidence_pack.missing_aspects)
        supplement_result = cast(
            EvidenceSupplementResult,
            self.subagent_runtime.execute(
            'evidence_agent',
            'supplement',
            EvidenceSupplementInput(
                task_id=task.task_id,
                query=f'{task.request.instructions}；补充维度：{"、".join(missing_aspects[:4])}',
                collection_name=task.request.collection_name,
                doc_ids=task.request.doc_ids,
                top_k=task.request.constraints.top_k,
                missing_aspects=missing_aspects,
                evidence_pack=evidence_pack,
            ),
            handoff=self._build_subagent_handoff(
                source_step_id='handle_evidence_gap',
                context_keys=['task.request.instructions', 'evidence_pack.missing_aspects', 'evidence_pack'],
                sandbox_profile='restricted',
            ),
            runner=lambda tool_name, payload: self._run_tool(
                tool_name,
                payload,
                workflow_state,
                fallback_factory=lambda exc: self._build_evidence_gap_fallback(
                    task,
                    list(payload.get('focus_aspects') or missing_aspects),
                    exc,
                ),
            ),
            merge_packs=self._merge_evidence_packs,
            ),
        )
        merged_pack = supplement_result.evidence_pack
        task.evidence_pack_id = f'ev-{task.task_id}'
        task.grounded_context = evidence_pack_to_grounded_context(
            objective=task.request.instructions,
            evidence_pack=merged_pack,
            evidence_pack_ref=task.evidence_pack_id,
        )
        task.graph_subgraph = evidence_pack_to_graph_subgraph(merged_pack)
        task.retrieval_quality_report = build_retrieval_quality_report(
            query=task.request.instructions,
            coverage_score=float(merged_pack.coverage_score),
            relevance_score=min(1.0, len(merged_pack.evidence_items) / max(1, task.request.constraints.top_k)),
            confidence_score=max(0.0, 1.0 - (0.15 * len(merged_pack.missing_aspects))),
            suggested_actions=['继续补证据'] if merged_pack.missing_aspects else [],
        )
        self.memory.upsert_task(task)
        self.memory.append_task_memory(
            task.task_id,
            'handle_evidence_gap',
            'evidence',
            f'执行局部重规划补证据，剩余缺口 {len(merged_pack.missing_aspects)} 项。',
            payload={
                'previous_missing_aspects': missing_aspects,
                'current_missing_aspects': merged_pack.missing_aspects,
                'coverage_score': merged_pack.coverage_score,
                'evidence_count': len(merged_pack.evidence_items),
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'handle_evidence_gap')
        completed_plan_step_ids, active_plan_step_id = self._complete_plan_step(workflow_state, 's2r')
        return {
            'task': task,
            'evidence_pack': merged_pack,
            'tool_call_count': int(workflow_state.get('tool_call_count') or 0) + len(supplement_result.selected_tools),
            'pending_plan_step_ids': pending_plan_step_ids,
            'completed_plan_step_ids': completed_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def analyze(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """基于证据提炼关键发现与风险，并建立 grounding 绑定。"""
        pending_plan_step_ids, active_plan_step_id = self._activate_plan_step(workflow_state, 's3')
        task = self._enter_step(workflow_state['task'], 'analyze')
        evidence_pack = workflow_state.get('evidence_pack')
        if evidence_pack is None:
            raise RuntimeError('evidence_pack is required before analyze')
        context_bundle = self.context_harness.build_context(workflow_state, 'analyze')
        
        extract_key_points_prompt = self.prompt_builder.render(
            step='analyze',
            context=context_bundle,
            instructions=task.request.instructions,
        )
        self.trace.record(
            'prompt_rendered',
            {
                'task_id': task.task_id,
                'step': 'analyze',
                'template_id': extract_key_points_prompt.template_id,
                'version': extract_key_points_prompt.version,
                'token_count': extract_key_points_prompt.token_count,
            },
        )
        self._record_prompt_render(task, context_bundle, extract_key_points_prompt)
        
        key_points = self._run_tool(
            'extract_key_points',
            {
                'instructions': task.request.instructions,
                'documents': list(context_bundle.state_slice.get('document_context_documents') or []),
                'evidence_pack': self._build_evidence_pack_payload(evidence_pack, context_bundle),
            },
            workflow_state,
            context_bundle=context_bundle,
        )
        task = self._refresh_task(task)
        
        extract_risks_prompt = self.prompt_builder.render(
            step='extract_risks',
            context=context_bundle,
            instructions=task.request.instructions,
        )
        self.trace.record(
            'prompt_rendered',
            {
                'task_id': task.task_id,
                'step': 'extract_risks',
                'template_id': extract_risks_prompt.template_id,
                'version': extract_risks_prompt.version,
                'token_count': extract_risks_prompt.token_count,
            },
        )
        self._record_prompt_render(task, context_bundle, extract_risks_prompt)
        
        risks = self._run_tool(
            'extract_risks',
            {
                'instructions': task.request.instructions,
                'evidence_pack': self._build_evidence_pack_payload(evidence_pack, context_bundle),
            },
            workflow_state,
            context_bundle=context_bundle,
        )
        task = self._refresh_task(task)
        
        analysis_data = key_points.model_dump(mode='json')
        grounding_result = self.grounding_engine.build_grounding_bundle(
            evidence_pack,
            analysis=analysis_data,
            draft_content=None,
        )
        
        self.memory.append_task_memory(
            task.task_id,
            'analyze',
            'analysis',
            f'完成分析，生成 {len(key_points.key_findings)} 条发现和 {len(risks.risks)} 条风险。',
            payload={
                'finding_count': len(key_points.key_findings),
                'risk_count': len(risks.risks),
                'open_questions': key_points.open_questions,
                'confidence': key_points.confidence,
                'alignment_score': grounding_result.alignment_score if grounding_result else 0.0,
                'coverage_ratio': grounding_result.coverage_ratio if grounding_result else 0.0,
                'unsupported_claim_count': grounding_result.unsupported_claim_count if grounding_result else 0,
                'prompt_version': extract_key_points_prompt.version,
                'grounding_runtime_version': 'v1',
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'analyze')
        completed_plan_step_ids, active_plan_step_id = self._complete_plan_step(workflow_state, 's3')
        return {
            'task': task,
            'analysis': analysis_data,
            'risks': risks.risks,
            'grounding_result': grounding_result.grounding_bundle if grounding_result else None,
            'tool_call_count': int(workflow_state.get('tool_call_count') or 0) + 2,
            'pending_plan_step_ids': pending_plan_step_ids,
            'completed_plan_step_ids': completed_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def draft_artifact(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """生成结构化报告草稿并保存为草稿产物，同时建立 grounding 绑定。

        该节点会渲染起草 prompt、调用报告子 Agent 生成草稿、执行 artifact guardrail/policy 校验，
        再把草稿持久化为任务产物，并记录草稿与证据之间的 grounding 结果。
        """
        pending_plan_step_ids, active_plan_step_id = self._activate_plan_step(workflow_state, 's4')
        task = self._enter_step(workflow_state['task'], 'draft_artifact')
        evidence_pack = workflow_state.get('evidence_pack')
        analysis = workflow_state.get('analysis') or {}
        if evidence_pack is None:
            raise RuntimeError('evidence_pack is required before draft_artifact')
        context_bundle = self.context_harness.build_context(workflow_state, 'draft_artifact')
        
        draft_prompt = self.prompt_builder.render(
            step='draft_artifact',
            context=context_bundle,
            instructions=task.request.instructions,
        )
        self.trace.record(
            'prompt_rendered',
            {
                'task_id': task.task_id,
                'step': 'draft_artifact',
                'template_id': draft_prompt.template_id,
                'version': draft_prompt.version,
                'token_count': draft_prompt.token_count,
            },
        )
        self._record_prompt_render(task, context_bundle, draft_prompt)
        
        analysis_slice = context_bundle.state_slice.get('analysis') or analysis
        risk_slice = list(context_bundle.state_slice.get('risks') or [])
        draft_result = cast(
            DraftArtifactResult,
            self.subagent_runtime.execute(
                'reporting_agent',
                'draft',
                DraftArtifactInput(
                    task_id=task.task_id,
                    summary=analysis_slice.get('summary') or '暂无摘要。',
                    key_findings=analysis_slice.get('key_findings') or [],
                    risks=risk_slice,
                    evidence=context_bundle.evidence_slice,
                    open_questions=analysis_slice.get('open_questions') or [],
                    confidence=analysis_slice.get('confidence') or 0.0,
                ),
                handoff=self._build_subagent_handoff(
                    source_step_id='draft_artifact',
                    context_keys=['analysis', 'risks', 'evidence_pack'],
                    sandbox_profile='thread_isolated',
                ),
                runner=lambda tool_name, payload: self._run_tool(
                    tool_name,
                    payload,
                    workflow_state,
                    context_bundle=context_bundle,
                    fallback_factory=lambda exc: self._build_draft_report_fallback(
                        task,
                        analysis,
                        evidence_pack,
                        workflow_state,
                        exc,
                    ),
                ),
            ),
        )
        draft = self.registry.get('draft_report').output_model(
            content=self._apply_artifact_metadata(draft_result.content, workflow_state)
        )
        task = self._refresh_task(task)
        
        grounding_result = self.grounding_engine.build_grounding_bundle(
            evidence_pack,
            analysis=analysis,
            draft_content=draft.content,
        )
        
        artifact_decision = self.guardrail_engine.validate_artifact(draft.content, stage='artifact')
        self.guardrail_engine.raise_runtime_error(artifact_decision)
        artifact_policy_decision = self.policy_engine.check_artifact(
            task.request,
            draft.content,
            coverage_score=evidence_pack.coverage_score,
        )
        self._raise_policy_error(artifact_policy_decision)
        artifact = self.memory.store_artifact(
            task.task_id,
            artifact_type=cast(str, workflow_state.get('artifact_type') or 'document_analysis_report'),
            status='draft',
            content=draft.content,
        )
        if artifact.artifact_id not in task.artifact_ids:
            task.artifact_ids.append(artifact.artifact_id)
        self.memory.upsert_task(task)
        self.memory.append_artifact_memory(
            task.task_id,
            artifact,
            summary=f'生成第 {artifact.version} 版报告草稿。',
        )
        self.trace.record(
            'task_artifact_stored',
            {
                'task_id': task.task_id,
                'artifact_id': artifact.artifact_id,
                'artifact_type': artifact.artifact_type,
                'version': artifact.version,
                'status': artifact.status,
                'alignment_score': grounding_result.alignment_score if grounding_result else 0.0,
                'unsupported_claim_count': grounding_result.unsupported_claim_count if grounding_result else 0,
            },
        )
        self.memory.append_task_memory(
            task.task_id,
            'draft_artifact',
            'state',
            '已生成结构化报告草稿。',
            payload={
                'artifact_id': artifact.artifact_id,
                'version': artifact.version,
                'alignment_score': grounding_result.alignment_score if grounding_result else 0.0,
                'unsupported_claim_count': grounding_result.unsupported_claim_count if grounding_result else 0,
                'grounding_runtime_version': 'v1',
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'draft_artifact')
        return {
            'task': task,
            'draft_content': draft.content,
            'draft_artifact_id': artifact.artifact_id,
            'grounding_result': grounding_result.grounding_bundle if grounding_result else None,
            'tool_call_count': int(workflow_state.get('tool_call_count') or 0) + len(draft_result.selected_tools),
            'pending_plan_step_ids': pending_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def review_artifact(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """对报告草稿进行审查，并按结果决定是否进入修订回路。

        审查节点除了使用 review agent 的显式审查结果，还会叠加 grounding engine 的未支撑结论
        检测结果，避免仅靠文字层面的审稿通过却遗漏证据支撑缺陷。
        """
        task = self._enter_step(workflow_state['task'], 'review_artifact')
        draft_content = workflow_state.get('draft_content')
        evidence_pack = workflow_state.get('evidence_pack')
        analysis = workflow_state.get('analysis') or {}
        if draft_content is None:
            raise RuntimeError('draft_content is required before review_artifact')
        context_bundle = self.context_harness.build_context(workflow_state, 'review_artifact')
        
        grounding_result = None
        if evidence_pack:
            grounding_result = self.grounding_engine.build_grounding_bundle(
                evidence_pack,
                analysis=analysis,
                draft_content=draft_content,
            )
        
        review_prompt = self.prompt_builder.render(
            step='review_artifact',
            context=context_bundle,
            grounding=grounding_result.grounding_bundle if grounding_result else None,
        )
        self.trace.record(
            'prompt_rendered',
            {
                'task_id': task.task_id,
                'step': 'review_artifact',
                'template_id': review_prompt.template_id,
                'version': review_prompt.version,
                'token_count': review_prompt.token_count,
            },
        )
        self._record_prompt_render(task, context_bundle, review_prompt)
        
        review_result = cast(
            ReviewDraftResult,
            self.subagent_runtime.execute(
            'review_agent',
            'review',
            ReviewDraftInput(task_id=task.task_id, content=draft_content),
            handoff=self._build_subagent_handoff(
                source_step_id='review_artifact',
                context_keys=['draft_content', 'evidence_pack', 'analysis'],
                sandbox_profile='thread_isolated',
            ),
            runner=lambda tool_name, payload: self._run_tool(
                tool_name,
                payload,
                workflow_state,
                context_bundle=context_bundle,
            ),
            ),
        )
        review = review_result.review
        
        unsupported_claims_from_grounding = grounding_result.grounding_bundle.unsupported_claims if grounding_result else []
        review.unsupported_claims.extend([claim for claim in unsupported_claims_from_grounding if claim not in review.unsupported_claims])
        
        task = self._refresh_task(task)
        self.memory.append_task_memory(
            task.task_id,
            'review_artifact',
            'review',
            '完成报告审查。',
            payload={
                **review.model_dump(mode='json'),
                'alignment_score': grounding_result.alignment_score if grounding_result else 0.0,
                'coverage_ratio': grounding_result.coverage_ratio if grounding_result else 0.0,
                'grounding_runtime_version': 'v1',
            },
        )
        self.trace.record(
            'task_review_completed',
            {
                'task_id': task.task_id,
                'passed': review.passed,
                'unsupported_claim_count': len(review.unsupported_claims),
                'missing_section_count': len(review.missing_sections),
                'alignment_score': grounding_result.alignment_score if grounding_result else 0.0,
                'coverage_ratio': grounding_result.coverage_ratio if grounding_result else 0.0,
            },
        )
        active_plan_step_id: str | None = cast(Optional[str], workflow_state.get('active_plan_step_id')) or 's4'
        pending_plan_step_ids = list(workflow_state.get('pending_plan_step_ids') or [])
        # 审查失败时只允许追加一次 revise loop，避免任务在审查阶段无限循环。
        if review_result.decision == 'revise':
            updated_plan = self.planner.replan_for_review(
                task.request,
                task.plan,
                review.missing_sections,
                review.unsupported_claims,
            )
            task.plan = updated_plan
            task = self.memory.append_plan_revision(
                task.task_id,
                trigger='review_failed',
                reason='报告审查未通过，进入一次受控 revise loop。',
                added_steps=['s4r'],
                plan=updated_plan,
            ) or task
            self.memory.append_task_memory(
                task.task_id,
                'review_artifact',
                'replan',
                '审查未通过，已追加一次局部修订计划。',
                payload={
                    'plan_version': task.plan_version,
                    'missing_sections': review.missing_sections,
                    'unsupported_claims': review.unsupported_claims,
                },
            )
            task = self.memory.append_reflection(
                task.task_id,
                step='review_artifact',
                trigger='review',
                decision='revise',
                summary='报告审查未通过，已进入一次受控 revise loop。',
                missing_sections=review.missing_sections,
                unsupported_claims=review.unsupported_claims,
                review_notes=review.review_notes,
                plan_version=task.plan_version,
            ) or task
            self.trace.record(
                'task_replanned',
                {
                    'task_id': task.task_id,
                    'trigger': 'review_failed',
                    'plan_version': task.plan_version,
                    'missing_sections': review.missing_sections,
                    'unsupported_claims': review.unsupported_claims,
                },
            )
            pending_plan_step_ids = self._prepend_pending_plan_step(
                pending_plan_step_ids,
                workflow_state,
                's4r',
            )
        else:
            task = self.memory.append_reflection(
                task.task_id,
                step='review_artifact',
                trigger='review',
                decision='finalize',
                summary='报告审查通过，进入最终交付阶段。',
                review_notes=review.review_notes,
                plan_version=task.plan_version,
            ) or task
        task = self._refresh_task(task)
        self._mark_progress(task, 'review_artifact')
        completed_plan_step_ids, active_plan_step_id = self._complete_plan_step(
            workflow_state,
            active_plan_step_id or 's4',
        )
        return {
            'task': task,
            'review': review,
            'tool_call_count': int(workflow_state.get('tool_call_count') or 0) + len(review_result.selected_tools),
            'plan': task.plan,
            'pending_plan_step_ids': pending_plan_step_ids,
            'completed_plan_step_ids': completed_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def revise_artifact(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """根据审查意见修订报告草稿。

        修订步骤复用 review agent 的 revise 能力，并在工具失败时提供模板化降级草稿，确保任务在
        可控范围内继续前进，而不是直接中断。
        """
        pending_plan_step_ids, active_plan_step_id = self._activate_plan_step(workflow_state, 's4r')
        task = self._enter_step(workflow_state['task'], 'revise_artifact')
        draft_content = workflow_state.get('draft_content')
        review = workflow_state.get('review')
        if draft_content is None or review is None:
            raise RuntimeError('draft_content and review are required before revise_artifact')
        context_bundle = self.context_harness.build_context(workflow_state, 'revise_artifact')
        revise_result = cast(
            ReviseDraftResult,
            self.subagent_runtime.execute(
            'review_agent',
            'revise',
            ReviseDraftInput(task_id=task.task_id, draft_content=draft_content, review=review),
            handoff=self._build_subagent_handoff(
                source_step_id='revise_artifact',
                context_keys=['draft_content', 'review'],
                sandbox_profile='thread_isolated',
            ),
            runner=lambda tool_name, payload: self._run_tool(
                tool_name,
                payload,
                workflow_state,
                context_bundle=context_bundle,
                fallback_factory=lambda exc: self._build_revised_draft_fallback_from_payload(
                    draft_content,
                    payload,
                    exc,
                ),
            ),
            ),
        )
        task = self._refresh_task(task)
        artifact = self.memory.store_artifact(
            task.task_id,
            artifact_type=cast(str, workflow_state.get('artifact_type') or 'document_analysis_report'),
            status='draft',
            content=self._apply_artifact_metadata(revise_result.content, workflow_state),
        )
        if artifact.artifact_id not in task.artifact_ids:
            task.artifact_ids.append(artifact.artifact_id)
        self.memory.upsert_task(task)
        self.memory.append_artifact_memory(
            task.task_id,
            artifact,
            summary=f'根据审查结果生成第 {artifact.version} 版修订草稿。',
            review_passed=False,
        )
        self.trace.record(
            'task_artifact_stored',
            {
                'task_id': task.task_id,
                'artifact_id': artifact.artifact_id,
                'artifact_type': artifact.artifact_type,
                'version': artifact.version,
                'status': artifact.status,
            },
        )
        self.memory.append_task_memory(
            task.task_id,
            'revise_artifact',
            'review',
            '已根据审查结果修订报告草稿。',
            payload={
                'artifact_id': artifact.artifact_id,
                'unsupported_claims': review.unsupported_claims,
                'missing_sections': review.missing_sections,
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'revise_artifact')
        return {
            'task': task,
            'draft_content': artifact.content,
            'draft_artifact_id': artifact.artifact_id,
            'revise_count': int(workflow_state.get('revise_count') or 0) + 1,
            'tool_call_count': int(workflow_state.get('tool_call_count') or 0) + len(revise_result.selected_tools),
            'pending_plan_step_ids': pending_plan_step_ids,
            'active_plan_step_id': active_plan_step_id,
        }

    def evaluate_exit_criteria(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """检查计划定义的退出条件是否全部满足。

        这里会把计划里声明的退出条件逐条翻译成具体检查逻辑，并决定下一步是直接交付、进入一次
        受控修订，还是因仍不满足条件而中止任务。
        """
        task = self._enter_step(workflow_state['task'], 'evaluate_exit_criteria')
        draft_content = workflow_state.get('draft_content')
        evidence_pack = workflow_state.get('evidence_pack')
        review = workflow_state.get('review')
        plan = workflow_state.get('plan') or task.plan
        if draft_content is None or evidence_pack is None or plan is None:
            raise RuntimeError('draft_content, evidence_pack and plan are required before evaluate_exit_criteria')
        failures = self._evaluate_exit_criteria(plan.exit_criteria, draft_content, evidence_pack, review)
        decision = 'finalize'
        # 当前实现最多允许一次修订回路；若仍不满足退出条件，则直接中止任务。
        if failures:
            decision = 'revise_artifact' if int(workflow_state.get('revise_count') or 0) < 1 else 'abort'
        self.memory.append_task_memory(
            task.task_id,
            'evaluate_exit_criteria',
            'state',
            '完成 exit criteria 校验。',
            payload={'failures': failures, 'decision': decision, 'exit_criteria': plan.exit_criteria},
        )
        task = self._refresh_task(task)
        if decision == 'abort':
            raise RuntimeError(f'exit criteria not satisfied: {"; ".join(failures[:4])}')
        self._mark_progress(task, 'evaluate_exit_criteria')
        return {
            'task': task,
            'exit_criteria_failures': failures,
            'exit_decision': decision,
        }

    def finalize(self, workflow_state: DocumentAnalysisState) -> DocumentAnalysisUpdate:
        """生成最终产物、更新任务指标并输出 `TaskResult`。

        这是任务工作流的收口节点，负责把草稿转成最终交付产物、补齐任务指标和结果对象，并把整轮
        任务执行状态沉淀为可查询的最终记录。
        """
        task = self._enter_step(workflow_state['task'], 'finalize')
        draft_content = workflow_state.get('draft_content')
        if draft_content is None:
            raise RuntimeError('draft_content is required before finalize')
        review = workflow_state.get('review')
        context_bundle = self.context_harness.build_context(workflow_state, 'finalize')
        final_content = self._run_tool(
            'finalize_report',
            {
                'content': draft_content.model_dump(mode='json'),
                'review': review.model_dump(mode='json') if isinstance(review, ReviewResult) else None,
                'output_format': task.request.output_format,
            },
            workflow_state,
            context_bundle=context_bundle,
        )
        final_content = self._apply_artifact_metadata(final_content, workflow_state)
        task = self._refresh_task(task)
        output_decision = self.guardrail_engine.validate_output(
            final_content,
            review=review if isinstance(review, ReviewResult) else None,
            output_format=task.request.output_format,
        )
        self.guardrail_engine.raise_runtime_error(output_decision)
        output_policy_decision = self.policy_engine.check_output(
            task.request,
            final_content,
            coverage_score=self._extract_coverage_score(workflow_state),
            review=review if isinstance(review, ReviewResult) else None,
        )
        self._raise_policy_error(output_policy_decision)
        final_artifact = self.memory.store_artifact(
            task.task_id,
            artifact_type=cast(str, workflow_state.get('artifact_type') or 'document_analysis_report'),
            status='final',
            content=final_content,
            review=review if isinstance(review, ReviewResult) else None,
        )
        if final_artifact.artifact_id not in task.artifact_ids:
            task.artifact_ids.append(final_artifact.artifact_id)
        task.final_artifact_id = final_artifact.artifact_id
        task.final_artifact = final_artifact
        task.status = 'completed'
        self.memory.upsert_task(task)
        self.memory.append_artifact_memory(
            task.task_id,
            final_artifact,
            summary=f'输出最终报告第 {final_artifact.version} 版。',
            review_passed=review.passed if isinstance(review, ReviewResult) else None,
        )
        self.trace.record(
            'task_artifact_stored',
            {
                'task_id': task.task_id,
                'artifact_id': final_artifact.artifact_id,
                'artifact_type': final_artifact.artifact_type,
                'version': final_artifact.version,
                'status': final_artifact.status,
            },
        )
        self.memory.append_task_memory(
            task.task_id,
            'finalize',
            'state',
            '任务完成并持久化最终产物。',
            payload={
                'final_artifact_id': final_artifact.artifact_id,
                'artifact_version': final_artifact.version,
            },
        )
        task = self._refresh_task(task)
        self._mark_progress(task, 'finalize')
        latency_ms = int((perf_counter() - workflow_state['started_at']) * 1000)
        task.metrics.step_count = len(task.completed_steps)
        task.metrics.tool_calls = int(workflow_state.get('tool_call_count') or 0) + 1
        task.metrics.latency_ms = latency_ms
        task.metrics.sub_agent_runs = len(task.sub_agent_runs)
        task.metrics.sub_agent_failures = sum(1 for item in task.sub_agent_runs if item.status == 'failed')
        scorecard, regression = self.evaluation_harness.evaluate_task(task)
        task.evaluation_scorecard = scorecard
        task.regression_result = regression
        self.memory.upsert_task(task)
        result = TaskResult(
            task_id=task.task_id,
            status='completed',
            final_artifact_id=final_artifact.artifact_id,
            metrics=task.metrics,
        )
        self.trace.record(
            'task_workflow_finalized',
            {
                'task_id': task.task_id,
                'step_count': task.metrics.step_count,
                'tool_calls': task.metrics.tool_calls,
                'latency_ms': latency_ms,
                'sub_agent_runs': task.metrics.sub_agent_runs,
                'sub_agent_failures': task.metrics.sub_agent_failures,
                'plan_version': task.plan_version,
                'artifact_count': len(task.artifact_ids),
                'task_memory_count': len(task.task_memory_entries),
                'artifact_memory_count': len(task.artifact_memory_entries),
                'reflection_count': len(task.reflection_entries),
                'plan_revision_count': len(task.plan_revisions),
                'final_review_passed': review.passed if isinstance(review, ReviewResult) else None,
                'unsupported_claim_count': len(review.unsupported_claims) if isinstance(review, ReviewResult) else 0,
            },
        )
        return {
            'task': task,
            'final_artifact_id': final_artifact.artifact_id,
            'result': result,
            'tool_call_count': task.metrics.tool_calls,
        }

    def _run_tool(
        self,
        name: str,
        payload: dict[str, Any],
        workflow_state: DocumentAnalysisState,
        context_bundle: ContextBundle | None = None,
        fallback_factory=None,
    ) -> Any:
        """执行工具，并在允许时进入降级/回退分支。

        Args:
            name: 工具名称。
            payload: 工具输入载荷。
            workflow_state: 当前工作流状态。
            fallback_factory: 可选回退结果构造器。

        Returns:
            工具执行结果或降级后的回退结果。
        """
        bundle = context_bundle or self.context_harness.build_context(workflow_state)
        latest_task = self._refresh_task(workflow_state['task'])
        latest_task.context_bundles[bundle.step_id] = bundle
        self.memory.upsert_task(latest_task)
        workflow_state['task'] = latest_task
        workflow_state['context_bundles'] = dict(latest_task.context_bundles)
        return self.execution_harness.run_tool(
            name,
            payload,
            workflow_state,
            bundle,
            failure_action=self._resolve_failure_branch(workflow_state['task'], name),
            fallback_factory=fallback_factory,
        )

    def _apply_artifact_metadata(
        self,
        content: ReportArtifactContent,
        workflow_state: DocumentAnalysisState,
    ) -> ReportArtifactContent:
        """把 skill 级 artifact 元数据补到统一报告内容上。"""
        artifact_title = cast(str, workflow_state.get('artifact_title') or '文档分析报告')
        updated = content.model_copy(deep=True)
        updated.title = artifact_title
        if updated.report_markdown is not None:
            updated.report_markdown = ArtifactFormatter.render_markdown(updated)
        if updated.report_json is not None:
            updated.report_json = ArtifactFormatter.render_json(updated)
        return updated

    def _record_prompt_render(
        self,
        task: TaskDetail,
        context_bundle: ContextBundle,
        render_result: PromptRenderResult,
    ) -> None:
        self.memory.append_task_memory(
            task.task_id,
            task.current_step or context_bundle.step_id,
            'state',
            f'已渲染提示词模板 {render_result.template_id}:{render_result.version}。',
            payload={
                'runtime_category': 'prompt',
                'prompt_template_id': render_result.template_id,
                'prompt_version': render_result.version,
                'prompt_step_type': render_result.step_type,
                'prompt_token_count': render_result.token_count,
                'context_step_id': context_bundle.step_id,
                'context_token_budget': context_bundle.token_budget,
                'tool_options': list(context_bundle.tool_options),
            },
        )
        prompt_template = self.prompt_builder.get_template(render_result.template_id, render_result.version)
        if prompt_template is None:
            return
        prompt_spec = prompt_template_to_spec(prompt_template)
        prompt_build_request = build_prompt_build_request(
            prompt_spec=prompt_spec,
            task_spec_ref=task.task_id,
            step_spec_ref=task.current_step or context_bundle.step_id,
            context_bundle_ref=context_bundle.step_id,
            tool_specs_ref=list(context_bundle.tool_options),
        )
        prompt_build_result = prompt_render_result_to_build_result(
            render_result,
            output_contract={'template_id': render_result.template_id, 'step_type': render_result.step_type},
            build_notes=[f'context_budget:{context_bundle.token_budget}'],
        )
        latest = self.memory.get_task(task.task_id)
        if latest is None:
            return
        latest.prompt_specs = [
            item
            for item in latest.prompt_specs
            if not (item.prompt_id == prompt_spec.prompt_id and item.prompt_version == prompt_spec.prompt_version)
        ]
        latest.prompt_specs.append(prompt_spec)
        latest.prompt_build_requests.append(prompt_build_request)
        latest.prompt_build_results.append(prompt_build_result)
        latest.prompt_build_requests = latest.prompt_build_requests[-50:]
        latest.prompt_build_results = latest.prompt_build_results[-50:]
        self.memory.upsert_task(latest)

    def _build_evidence_pack_payload(
        self,
        evidence_pack: EvidencePack,
        context_bundle: ContextBundle,
    ) -> dict[str, Any]:
        """基于 Context Bundle 生成当前步骤可消费的证据包载荷。

        这里不会盲目把整份证据包全部下发，而是优先使用当前上下文切片中的证据视图，避免 prompt
        和子 Agent 输入在步骤间无限膨胀。
        """
        return {
            'task_id': evidence_pack.task_id,
            'evidence_items': context_bundle.evidence_slice,
            'coverage_score': evidence_pack.coverage_score,
            'missing_aspects': list(evidence_pack.missing_aspects),
        }

    def _extract_coverage_score(self, workflow_state: DocumentAnalysisState) -> float:
        """从当前工作流状态中提取证据覆盖率。"""
        evidence_pack = workflow_state.get('evidence_pack')
        if evidence_pack is None:
            return 0.0
        return float(evidence_pack.coverage_score)

    def _build_subagent_handoff(
        self,
        *,
        source_step_id: str,
        context_keys: list[str],
        sandbox_profile: str,
    ) -> SubAgentHandoff:
        """构造统一的子 Agent 交接信息。

        handoff 里显式限制步骤数和预算，目的是让子 Agent 只在当前节点的局部任务边界内工作，
        避免越权扩张执行范围。
        """
        return SubAgentHandoff(
            source_step_id=source_step_id,
            context_keys=context_keys,
            step_limit=1,
            budget_limit=2,
            sandbox_profile=sandbox_profile,
        )

    def _raise_policy_error(self, decision) -> None:
        """在策略校验不通过时抛出统一格式异常。"""
        if decision.allowed:
            return
        raise RuntimeError(f'policy::{decision.policy_name}: {decision.reason}')

    def _build_document_context_fallback(
        self,
        task: TaskDetail,
        exc: ToolExecutionError,
    ) -> DocumentContextResult:
        """构造文档上下文加载失败时的降级结果。"""
        documents: list[DocumentContextItem] = []
        for doc_id in task.request.doc_ids:
            record = self.state.documents.get(doc_id)
            if record is None:
                continue
            documents.append(
                DocumentContextItem(
                    doc_id=doc_id,
                    title=str(record.get('document_title') or record.get('file_name') or doc_id),
                    summary=str(record.get('document_summary') or '').strip() or f'文档上下文降级加载：{exc.message}',
                    sections=[],
                    metadata={'fallback': True},
                )
            )
        return DocumentContextResult(documents=documents)

    def _build_evidence_gap_fallback(
        self,
        task: TaskDetail,
        focus_aspects: list[str],
        exc: ToolExecutionError,
    ) -> EvidencePack:
        """构造证据检索失败时的空证据包降级结果。"""
        missing_aspects = focus_aspects or ['证据检索失败']
        return EvidencePack(
            task_id=task.task_id,
            evidence_items=[],
            coverage_score=0.0,
            missing_aspects=[*missing_aspects[:5], f'fallback:{exc.code}'][:6],
        )

    def _build_draft_report_fallback(
        self,
        task: TaskDetail,
        analysis: dict[str, Any],
        evidence_pack: EvidencePack,
        workflow_state: DocumentAnalysisState,
        exc: ToolExecutionError,
    ):
        """构造草稿生成失败时的模板化回退草稿。"""
        summary = str(analysis.get('summary') or '报告草稿生成失败，已回退为模板化输出。').strip()
        open_questions = list(analysis.get('open_questions') or [])
        fallback_hint = f'草稿生成降级：{exc.code}'
        if fallback_hint not in open_questions:
            open_questions.append(fallback_hint)
        content = ReportArtifactContent(
            summary=summary,
            key_findings=list(analysis.get('key_findings') or []),
            risks=[item for item in workflow_state.get('risks') or []],
            evidence=[item for item in evidence_pack.evidence_items],
            open_questions=open_questions,
            confidence=float(analysis.get('confidence') or 0.0),
            report_markdown=f'# 文档分析报告\n\n{summary}\n\n- 降级原因：{exc.message}\n',
            report_json={
                'summary': summary,
                'open_questions': open_questions,
                'fallback_reason': exc.code,
            },
        )
        tool = self.registry.get('draft_report')
        return tool.output_model(content=content)

    def _build_revised_draft_fallback(
        self,
        draft_content: ReportArtifactContent,
        key_findings: list[Any],
        risks: list[Any],
        open_questions: list[str],
        exc: ToolExecutionError,
    ):
        """构造修订阶段失败时的模板化回退草稿。"""
        content = ReportArtifactContent(
            summary=draft_content.summary,
            key_findings=key_findings,
            risks=risks,
            evidence=draft_content.evidence,
            open_questions=[*open_questions, f'修订降级：{exc.code}'],
            confidence=max(0.1, draft_content.confidence - 0.1),
            report_markdown=(
                f"# 文档分析报告\n\n{draft_content.summary}\n\n- 修订阶段进入模板化降级分支：{exc.message}\n"
            ),
            report_json={
                'summary': draft_content.summary,
                'open_questions': [*open_questions, f'修订降级：{exc.code}'],
                'fallback_reason': exc.code,
            },
        )
        tool = self.registry.get('draft_report')
        return tool.output_model(content=content)

    def _build_revised_draft_fallback_from_payload(
        self,
        draft_content: ReportArtifactContent,
        payload: dict[str, Any],
        exc: ToolExecutionError,
    ):
        """根据 revise 工具载荷构造修订回退草稿。"""
        return self._build_revised_draft_fallback(
            draft_content,
            list(payload.get('key_findings') or []),
            list(payload.get('risks') or []),
            list(payload.get('open_questions') or []),
            exc,
        )

    def _resolve_failure_branch(self, task: TaskDetail, tool_name: str) -> str | None:
        """从计划定义中解析工具失败后的分支策略。

        这样做可以把“某个工具失败后该重试、跳过还是转到补证据分支”的规则保留在计划层，而不是
        硬编码在具体节点实现里。
        """
        if task.plan is None:
            return None
        for step in task.plan.steps:
            if tool_name in step.candidate_tools:
                return step.failure_branch
            composite_tool_name = step.tool_name.replace(' ', '')
            if tool_name in composite_tool_name:
                return step.failure_branch
        return None

    def _peek_pending_plan_step(self, workflow_state: DocumentAnalysisState) -> str | None:
        """读取当前待执行计划步骤队列头部。"""
        pending_plan_step_ids = list(workflow_state.get('pending_plan_step_ids') or [])
        if not pending_plan_step_ids:
            return None
        return pending_plan_step_ids[0]

    def _activate_plan_step(
        self,
        workflow_state: DocumentAnalysisState,
        step_id: str,
    ) -> tuple[list[str], str | None]:
        """把指定步骤标记为当前激活步骤，并从待执行队列移除。

        该 helper 负责维护计划步骤队列的“消费”语义，避免节点执行成功前后都手工改写待执行列表。
        """
        pending_plan_step_ids = list(workflow_state.get('pending_plan_step_ids') or [])
        if pending_plan_step_ids and pending_plan_step_ids[0] == step_id:
            pending_plan_step_ids = pending_plan_step_ids[1:]
        elif step_id in pending_plan_step_ids:
            pending_plan_step_ids = [item for item in pending_plan_step_ids if item != step_id]
        return pending_plan_step_ids, step_id

    def _complete_plan_step(
        self,
        workflow_state: DocumentAnalysisState,
        step_id: str,
    ) -> tuple[list[str], None]:
        """把指定步骤追加到已完成步骤列表，并清空当前激活步骤。"""
        completed_plan_step_ids = list(workflow_state.get('completed_plan_step_ids') or [])
        if step_id and step_id not in completed_plan_step_ids:
            completed_plan_step_ids.append(step_id)
        return completed_plan_step_ids, None

    def _prepend_pending_plan_step(
        self,
        pending_plan_step_ids: list[str],
        workflow_state: DocumentAnalysisState,
        step_id: str,
    ) -> list[str]:
        """在满足条件时把步骤重新插回待执行队列头部。

        这主要用于 review 失败后的局部 replan 场景，让新增的修订步骤优先执行，同时避免和已经
        完成或当前正在执行的步骤重复。
        """
        completed_plan_step_ids = set(workflow_state.get('completed_plan_step_ids') or [])
        active_plan_step_id = workflow_state.get('active_plan_step_id')
        if step_id in completed_plan_step_ids or step_id == active_plan_step_id:
            return pending_plan_step_ids
        if step_id in pending_plan_step_ids:
            return pending_plan_step_ids
        return [step_id, *pending_plan_step_ids]

    def _evaluate_exit_criteria(
        self,
        exit_criteria: list[str],
        draft_content: ReportArtifactContent,
        evidence_pack: EvidencePack,
        review: ReviewResult | None,
    ) -> list[str]:
        """检查退出条件并返回未满足项列表。

        退出条件本质上是一组声明式字符串规则；这里把它们翻译为具体校验逻辑，并生成可直接写入
        任务记忆和错误信息的失败列表。
        """
        failures: list[str] = []
        evidence_ids = {item.citation_id for item in draft_content.evidence}
        evidence_text = '\n'.join(item.text for item in evidence_pack.evidence_items).lower()
        disclosure_text = '\n'.join(
            [
                draft_content.summary,
                draft_content.report_markdown or '',
                ' '.join(draft_content.open_questions),
            ]
        ).lower()
        for criterion in exit_criteria:
            normalized = criterion.strip()
            if not normalized:
                continue
            if normalized == '报告字段完整':
                if not draft_content.summary.strip() or draft_content.report_markdown is None or draft_content.report_json is None:
                    failures.append(normalized)
                continue
            if normalized == '关键结论有证据支撑':
                if not self._claims_are_grounded(draft_content, evidence_ids, review):
                    failures.append(normalized)
                continue
            if normalized.startswith('覆盖重点维度：'):
                aspects = [item.strip() for item in normalized.split('：', 1)[1].split('、') if item.strip()]
                uncovered = [item for item in aspects if item.lower() not in evidence_text and item.lower() not in disclosure_text]
                if uncovered:
                    failures.append(f'{normalized} -> 未覆盖: {"、".join(uncovered[:4])}')
                continue
            if normalized.startswith('显式披露证据缺口：'):
                aspects = [item.strip() for item in normalized.split('：', 1)[1].split('、') if item.strip()]
                undisclosed = [
                    item
                    for item in aspects
                    if item.lower() not in disclosure_text
                    and not (
                        item.startswith('fallback:')
                        and any(keyword in disclosure_text for keyword in ['降级', '证据不足', '待确认'])
                    )
                ]
                if undisclosed:
                    failures.append(f'{normalized} -> 未披露: {"、".join(undisclosed[:4])}')
                continue
            if normalized.startswith('补齐缺失字段：'):
                fields = [item.strip() for item in normalized.split('：', 1)[1].split('、') if item.strip()]
                missing = [item for item in fields if not self._report_field_present(draft_content, item)]
                if missing:
                    failures.append(f'{normalized} -> 未补齐: {"、".join(missing[:4])}')
                continue
            if normalized == 'unsupported claims 为 0':
                if review is not None and review.unsupported_claims:
                    failures.append(normalized)
                continue
        return failures

    def _claims_are_grounded(
        self,
        draft_content: ReportArtifactContent,
        evidence_ids: set[str],
        review: ReviewResult | None,
    ) -> bool:
        """检查关键发现和风险是否均被证据支撑。

        若当前草稿已经显式披露为降级/证据不足结果，则允许通过；否则要求关键发现与风险引用都能
        在现有证据集中闭合。
        """
        report_json = draft_content.report_json or {}
        fallback_reason = report_json.get('fallback_reason') if isinstance(report_json, dict) else None
        fallback_disclosed = bool(fallback_reason) or any(
            keyword in f'{draft_content.summary}\n{draft_content.report_markdown or ""}'
            for keyword in ['降级', '证据不足', '待确认']
        )
        if fallback_disclosed:
            return True
        if review is not None and review.unsupported_claims:
            return False
        for item in draft_content.key_findings:
            if item.citation_ids and not set(item.citation_ids).issubset(evidence_ids):
                return False
        for risk in draft_content.risks:
            if risk.citation_ids and not set(risk.citation_ids).issubset(evidence_ids):
                return False
        return True

    def _report_field_present(self, draft_content: ReportArtifactContent, field_name: str) -> bool:
        """判断报告内容中某个字段是否已经补齐。"""
        if field_name == 'summary':
            return bool(draft_content.summary.strip())
        if field_name == 'key_findings':
            return bool(draft_content.key_findings)
        if field_name == 'risks':
            return bool(draft_content.risks)
        if field_name == 'open_questions':
            return bool(draft_content.open_questions)
        if field_name == 'report_markdown':
            return draft_content.report_markdown is not None
        if field_name == 'report_json':
            return draft_content.report_json is not None
        return False

    def _mark_progress(self, task: TaskDetail, step_name: str) -> None:
        """记录步骤完成并写入 trace。

        该 helper 同时更新任务对象、任务运行态和运行事件，是任务版步骤生命周期“完成态”的统一
        收口入口。
        """
        if step_name not in task.completed_steps:
            task.completed_steps.append(step_name)
        task.current_step = step_name
        task.ensure_runtime_contracts()
        if task.task_run is not None:
            mark_step_completed(task.task_run, step_name, completed_step_ids=list(task.completed_steps))
        task.run_events.append(
            create_run_event(
                name='workflow_step_completed',
                payload={
                    'task_id': task.task_id,
                    'task_run_id': task.task_run.run_id if task.task_run is not None else None,
                    'task_step_id': step_name,
                    'completed_step_ids': list(task.completed_steps),
                },
            )
        )
        self.memory.upsert_task(task)
        self.trace.record('task_step_completed', {'task_id': task.task_id, 'step': step_name})

    def _enter_step(self, task: TaskDetail, step_name: str) -> TaskDetail:
        """将任务切换到指定步骤，并写入 started 运行事件。

        这是任务版步骤生命周期“开始态”的统一入口，负责推进 `current_step`、递增尝试次数并持久化。
        """
        task.current_step = step_name
        task.ensure_runtime_contracts()
        if task.task_run is not None:
            runtime = mark_step_started(task.task_run, step_name)
        else:
            runtime = None
        task.run_events.append(
            create_run_event(
                name='workflow_step_started',
                payload={
                    'task_id': task.task_id,
                    'task_run_id': task.task_run.run_id if task.task_run is not None else None,
                    'task_step_id': step_name,
                    'attempt_count': runtime.attempt_count if runtime is not None else 0,
                },
            )
        )
        self.memory.upsert_task(task)
        return task

    def mark_step_failed(self, task: TaskDetail, step_name: str, error: str) -> TaskDetail:
        """把当前步骤更新为 failed，并写入失败运行事件。"""
        task.current_step = step_name
        task.ensure_runtime_contracts()
        if task.task_run is not None:
            mark_step_failed(task.task_run, step_name, completed_step_ids=list(task.completed_steps), error=error)
        task.run_events.append(
            create_run_event(
                name='workflow_step_failed',
                payload={
                    'task_id': task.task_id,
                    'task_run_id': task.task_run.run_id if task.task_run is not None else None,
                    'task_step_id': step_name,
                    'error': error,
                },
            )
        )
        return self.memory.upsert_task(task)

    def append_checkpoint(
        self,
        task: TaskDetail,
        *,
        step_id: str,
        next_route: str,
        state_snapshot: dict[str, Any],
    ) -> TaskDetail:
        """为 task runtime 追加一个可 replay checkpoint。

        checkpoint 会记录当前步骤、下一跳路由和最小状态快照，供任务重放与恢复逻辑复用。
        """
        task.ensure_runtime_contracts()
        checkpoint = create_checkpoint(
            step_id=step_id,
            next_route=next_route,
            completed_step_ids=list(task.completed_steps),
            state_snapshot=state_snapshot,
        )
        if task.task_run is not None:
            task.task_run.checkpoints = [*task.task_run.checkpoints, checkpoint]
        task.run_events.append(
            create_run_event(
                name='task_checkpoint_created',
                timestamp=checkpoint.created_at,
                payload={
                    'task_id': task.task_id,
                    'task_run_id': task.task_run.run_id if task.task_run is not None else None,
                    'checkpoint_id': checkpoint.checkpoint_id,
                    'task_step_id': step_id,
                    'next_route': next_route,
                    'completed_step_ids': list(checkpoint.completed_step_ids),
                },
            )
        )
        return self.memory.upsert_task(task)

    def _refresh_task(self, task: TaskDetail) -> TaskDetail:
        """从任务内存中刷新任务对象，避免使用过期快照。"""
        return self.memory.get_task(task.task_id) or task

    def _merge_evidence_packs(self, primary: EvidencePack, secondary: EvidencePack) -> EvidencePack:
        """合并两份证据包，并优先保留支撑度更高的证据项。

        该合并策略用于多轮补证据或失败降级后重新收口结果，避免相同 chunk 被低质量结果覆盖。
        """
        evidence_by_chunk: dict[str, Any] = {}
        for item in [*primary.evidence_items, *secondary.evidence_items]:
            existing = evidence_by_chunk.get(item.chunk_id)
            if existing is None or item.support_score > existing.support_score:
                evidence_by_chunk[item.chunk_id] = item
        missing_aspects = [item for item in primary.missing_aspects if item not in set(secondary.missing_aspects)]
        return EvidencePack(
            task_id=primary.task_id,
            evidence_items=list(evidence_by_chunk.values())[: max(len(primary.evidence_items), len(secondary.evidence_items), 1)],
            coverage_score=max(primary.coverage_score, secondary.coverage_score),
            missing_aspects=missing_aspects,
        )

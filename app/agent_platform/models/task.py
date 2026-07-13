"""任务模型模块�?
负责定义文档分析任务从请求、规划、执行、反思到最终结果的全套数据模型，作为任�?API�?任务运行时、工作流节点和存储层之间共享的数据契约�?"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.agent_platform.harness.models import ContextBundle
from app.models.artifact import Artifact
from app.models.runtime_contracts import GraphSubgraph, GroundedContext, MemoryRecord, PromptBuildRequest, PromptBuildResult, PromptSpec, ResultContract
from app.rag_system.knowledge.contracts import RetrievalQualityReport

# 任务主状态与运行态共享这一组状态枚举，避免 API、worker、workflow 和持久化层各自发明一套状态名�?TaskStatus = Literal['queued', 'running', 'completed', 'failed']


class TaskConstraints(BaseModel):
    """任务执行约束�?
    用于限制任务步数、输出语言和默认检索规模，避免任务在运行时无限扩张�?    """

    max_steps: int = Field(default=8, ge=1, le=16)
    language: str = 'zh-CN'
    top_k: int = Field(default=6, ge=1, le=20)


class RunBudget(BaseModel):
    """统一描述一次任务运行的硬预算�?
    该模型属于平台层约束，不直接表达领域语义，而是限制 workflow 在步骤数、单步轮次和工具调用�?    上的最坏开销�?    """

    max_steps: int = Field(default=8, ge=1, le=32)
    max_step_turns: int = Field(default=2, ge=1, le=8)
    max_tool_calls: int = Field(default=16, ge=1, le=128)
    top_k: int = Field(default=6, ge=1, le=20)


class TaskRequest(BaseModel):
    """面向 Document Analysis Agent 的标准任务请求�?
    它是任务系统最外层的入口模型，既表达用户想做什么，也声明权限边界和运行约束。后�?planner�?    workflow 和存储层都会围绕它派生出更细的运行时契约�?    """

    task_type: str = 'document_analysis'
    collection_name: str = Field(min_length=1)
    doc_ids: list[str] = Field(default_factory=list)
    instructions: str = Field(min_length=1)
    output_format: Literal['markdown', 'json', 'markdown+json'] = 'markdown+json'
    organization_id: str | None = None
    tenant_id: str | None = None
    requester_role: str | None = None
    permission_scope: str | None = None
    allowed_permissions: list[str] = Field(default_factory=list)
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)

    @field_validator('collection_name', 'instructions')
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        """清理必填文本字段首尾空白，并阻断空字符串�?""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned

    @field_validator('task_type')
    @classmethod
    def _normalize_task_type(cls, value: str) -> str:
        """标准�?task_type，确保任务面可扩展�?""
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned

    @field_validator('doc_ids')
    @classmethod
    def _normalize_doc_ids(cls, value: list[str]) -> list[str]:
        """标准化文�?ID 列表并按声明顺序去重�?""
        doc_ids: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = str(item).strip()
            if not cleaned or cleaned in seen:
                continue
            doc_ids.append(cleaned)
            seen.add(cleaned)
        return doc_ids

    @field_validator('organization_id', 'tenant_id', 'requester_role', 'permission_scope')
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        """清理可选文本字段，空串统一归一�?`None`�?""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator('allowed_permissions')
    @classmethod
    def _normalize_allowed_permissions(cls, value: list[str]) -> list[str]:
        """标准化显式权限列表并去重�?""
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = str(item).strip().lower()
            if not cleaned or cleaned in seen:
                continue
            normalized.append(cleaned)
            seen.add(cleaned)
        return normalized

    def to_run_budget(self) -> RunBudget:
        """把请求侧约束映射为平台层运行预算�?
        这里会对用户输入做一次平台上限裁剪，避免请求直接把执行预算扩张到不可控范围�?        """
        max_steps = max(1, min(int(self.constraints.max_steps), 32))
        return RunBudget(
            max_steps=max_steps,
            max_step_turns=2,
            max_tool_calls=max_steps * 2,
            top_k=self.constraints.top_k,
        )


class TaskStep(BaseModel):
    """规划后的单个任务步骤�?
    描述单步意图、可用工具、失败分支和成功条件，供规划器与执行器共享�?    """

    step_id: str
    intent: str
    tool_name: str
    required_inputs: list[str] = Field(default_factory=list)
    candidate_tools: list[str] = Field(default_factory=list)
    produced_artifacts: list[str] = Field(default_factory=list)
    failure_branch: Literal['retry', 'fallback', 'degrade', 'skip_with_gap', 'abort'] = 'abort'
    success_condition: str

    def to_step_spec(self) -> StepSpec:
        """�?workflow 步骤映射为平台层 StepSpec�?""
        allowed_tools = self.candidate_tools or [self.tool_name]
        return StepSpec(
            step_id=self.step_id,
            objective=self.intent,
            allowed_tools=allowed_tools,
            max_turns=2,
            success_criteria=[self.success_condition],
            stop_conditions=['success_criteria_satisfied', 'max_turns_reached'],
            fallback_action=self.failure_branch,
            output_schema={
                'produced_artifacts': list(self.produced_artifacts),
                'required_inputs': list(self.required_inputs),
            },
        )


class StepSpec(BaseModel):
    """平台层的有界步骤契约�?
    �?`TaskStep` 相比，它更偏运行时语义：强调允许哪些工具、最多转几轮、何时视为成功�?    失败时允许采取什么回退动作�?    """

    step_id: str
    objective: str
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=2, ge=1, le=8)
    success_criteria: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    fallback_action: Literal['retry', 'fallback', 'degrade', 'skip_with_gap', 'abort'] = 'abort'
    output_schema: dict[str, Any] = Field(default_factory=dict)


class TaskPlan(BaseModel):
    """有界规划结果�?
    用于表达一次任务在受控步数内的执行蓝图�?    """

    goal: str
    expected_artifact: str
    max_steps: int
    steps: list[TaskStep] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)

    def to_step_specs(self) -> list[StepSpec]:
        """把计划步骤转换为统一 StepSpec 列表�?""
        return [step.to_step_spec() for step in self.steps]


class TaskSpec(BaseModel):
    """平台层统一任务定义�?
    该模型是任务系统�?workflow 层真正消费的“任务规格书”，由请求、计划或 skill adapter 投影而来�?    """

    task_type: str
    objective: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    run_budget: RunBudget = Field(default_factory=RunBudget)
    steps: list[StepSpec] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class TaskRun(BaseModel):
    """平台层统一运行态对象�?
    `TaskSpec` 描述“应该怎么做”，`TaskRun` 描述“这次实际做到哪一步了”。两者组合后，才能完整表�?    一次任务执行的计划和进度�?    """

    run_id: str
    task_id: str
    status: Literal['queued', 'running', 'completed', 'failed'] = 'queued'
    current_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    step_attempts: dict[str, int] = Field(default_factory=dict)
    budget: RunBudget = Field(default_factory=RunBudget)
    step_specs: list[StepSpec] = Field(default_factory=list)
    step_runtimes: dict[str, 'StepRuntimeRecord'] = Field(default_factory=dict)
    checkpoints: list['CheckpointRecord'] = Field(default_factory=list)
    last_reflection_decision: ReflectionDecision | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def start(self, started_at: datetime) -> None:
        """把运行态切换为 `running`�?""
        self.status = 'running'
        self.started_at = started_at

    def sync_progress(
        self,
        *,
        current_step_id: str | None,
        completed_step_ids: list[str],
        completed_at: datetime | None = None,
        failed: bool = False,
    ) -> None:
        """根据任务详情同步运行态进度�?
        该方法主要服务于任务详情模型和运行态模型之间的对齐，避免两边各自维护进度导致漂移�?        """
        self.current_step_id = current_step_id
        self.completed_step_ids = list(completed_step_ids)
        if current_step_id is not None:
            self.step_attempts[current_step_id] = int(self.step_attempts.get(current_step_id, 0)) + 1
        if completed_at is not None:
            self.completed_at = completed_at
            self.status = 'failed' if failed else 'completed'


class StepRuntimeRecord(BaseModel):
    """记录单个 step 在一次运行中的执行状态�?
    这是步骤粒度的最小运行态单元，专门用来回答“这个步骤跑了几次、何时开�?结束、是否降级�?    为什么退出”�?    """

    step_id: str
    status: Literal['pending', 'running', 'completed', 'failed', 'skipped'] = 'pending'
    attempt_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_reason: str | None = None
    fallback_action_applied: Literal['retry', 'fallback', 'degrade', 'skip_with_gap', 'abort'] | None = None
    degraded: bool = False
    skipped: bool = False


class CheckpointRecord(BaseModel):
    """记录一次可重放的运行时 checkpoint�?
    checkpoint 同时�?query workflow �?task workflow 复用，因此字段设计保持通用：记录当前步骤�?    下一跳路由、已完成步骤和最小可恢复状态快照�?    """

    checkpoint_id: str
    step_id: str
    next_route: str
    created_at: datetime
    completed_step_ids: list[str] = Field(default_factory=list)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)


class ReflectionDecision(BaseModel):
    """记录一次结构化 reflection 决策�?
    该模型主要服务于运行态自反思和恢复决策，既能表达“接�?继续”，也能表达“重检�?重规�?保守改写�?    这类带回退动作的分支判断�?    """

    decision: Literal['accept', 'retry_retrieve', 'rewrite_answer']
    reason: str | None = None
    should_continue: bool = False
    fallback_action: Literal['retry', 'fallback', 'degrade', 'skip_with_gap', 'abort'] | None = None
    exit_reason: str | None = None
    confidence: float | None = None
    risk: str | None = None
    supported: bool | None = None
    final_mode: str | None = None


class TaskRunEvent(BaseModel):
    """task runtime 的结构化运行事件�?
    这类事件偏向运行审计和恢复，不是前端流式协议；它们通常会被持久化并转译�?memory record�?    """

    event_id: str
    name: str
    timestamp: datetime
    payload: dict[str, Any]


class PlanRevision(BaseModel):
    """记录局部重规划历史�?
    当审查失败、证据不足或其他策略触发 replan 时，这个模型用于沉淀“为什么改计划、改了什么”�?    """

    version: int = Field(default=1, ge=1)
    trigger: str
    reason: str
    added_steps: list[str] = Field(default_factory=list)
    created_at: datetime


class TaskMemoryEntry(BaseModel):
    """记录任务执行过程中的中间状态�?
    �?`TaskRunEvent` 相比，它更强调面向人和后续智能体可读的摘要视角，而不是单纯运行审计�?    """

    entry_id: str
    step: str
    kind: Literal['context', 'evidence', 'analysis', 'review', 'replan', 'state']
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ArtifactMemoryEntry(BaseModel):
    """记录任务产物的版本演进�?
    该模型让草稿、修订稿和最终稿之间的版本脉络可追溯，便于回看任务是如何逐步收敛到最终交付物的�?    """

    artifact_id: str
    artifact_type: str
    version: int = Field(default=1, ge=1)
    status: Literal['draft', 'final'] = 'draft'
    summary: str
    review_passed: bool | None = None
    created_at: datetime


class ReflectionEntry(BaseModel):
    """记录一次任务级反思决策�?
    它与 `ReflectionDecision` 的区别在于：前者更偏运行时即时分支，后者更偏任务轨迹里的长期记录�?    """

    entry_id: str
    step: str
    trigger: Literal['evidence_gap', 'review']
    decision: Literal['continue', 'replan', 'revise', 'finalize']
    summary: str
    missing_aspects: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime


class ToolCallRecord(BaseModel):
    """记录工具调用历史�?
    用于后续分析任务失败原因、工具使用模式和重试成本�?    """

    tool_call_id: str
    tool_name: str
    step: str | None = None
    status: Literal['ok', 'error']
    error_type: str | None = None
    default_action: str | None = None
    retry_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    input_preview: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime


class SubAgentRunRecord(BaseModel):
    """记录一次受控子代理执行�?
    该模型用于回答“哪个子 Agent 在什么上下文下做了什么、用了哪些工具、是否触发了回退”�?    """

    run_id: str
    agent_name: str
    action: str
    status: Literal['completed', 'fallback', 'failed'] = 'completed'
    handoff_id: str | None = None
    source_step_id: str | None = None
    context_keys: list[str] = Field(default_factory=list)
    step_limit: int | None = Field(default=None, ge=1)
    budget_limit: int | None = Field(default=None, ge=1)
    sandbox_profile: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TaskMetrics(BaseModel):
    """任务执行指标�?
    保存一轮任务运行最基础的成本与稳定性指标，供结果摘要、列表接口和轻量分析复用�?    """

    step_count: int = 0
    tool_calls: int = 0
    latency_ms: int = 0
    sub_agent_runs: int = 0
    sub_agent_failures: int = 0


class TaskEvaluationScorecard(BaseModel):
    """单次任务运行生成的在线评测记分卡�?
    这份记分卡偏向在线质量评估视角，用于量化产物完整性、grounding 质量、工具稳定性和整体分数�?    """

    task_id: str
    policy_name: str = 'document_analysis_default'
    policy_version: str = 'v1'
    scorecard_version: str = 'v1'
    artifact_completeness: float = 0.0
    grounding_score: float = 0.0
    coverage_score: float = 0.0
    review_score: float = 0.0
    execution_stability_score: float = 0.0
    avg_tool_latency_ms: float = 0.0
    tool_retry_rate: float = 0.0
    tool_failure_rate: float = 0.0
    tool_fallback_rate: float = 0.0
    unsupported_claim_rate: float = 0.0
    task_success_rate: float = 0.0
    avg_cost_per_task: float = 0.0
    overall_score: float = 0.0
    regression_baseline: str | None = None
    baseline_kind: Literal['none', 'task', 'version', 'benchmark', 'report'] = 'none'
    baseline_reference: str | None = None
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime


class TaskRegressionResult(BaseModel):
    """描述当前任务相对基线任务的轻量回归对比结果�?
    该模型不直接存明细样本，而是保存相对基线的结论和关键指标差异，方便快速判断是否退化�?    """

    baseline_task_id: str | None = None
    baseline_kind: Literal['none', 'task', 'version', 'benchmark', 'report'] = 'none'
    baseline_reference: str | None = None
    status: Literal['none', 'pass', 'warn', 'fail'] = 'none'
    reasons: list[str] = Field(default_factory=list)
    metric_deltas: dict[str, float] = Field(default_factory=dict)
    compared_at: datetime


class TaskFailure(BaseModel):
    """任务失败信息�?
    用于把失败原因沉淀为结构化字段，而不是只保留一段自由文本�?    """

    step_id: str | None = None
    code: str
    message: str


class TaskResult(BaseModel):
    """任务的执行结果摘要�?
    用于运行时在任务完成或失败时返回轻量结果�?    """

    task_id: str
    status: Literal['completed', 'failed']
    final_artifact_id: str | None = None
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)


class TaskDetail(BaseModel):
    """任务完整状态与执行上下文�?
    该模型聚合任务请求、计划、执行轨迹、失败信息、产物关联和租约字段，是任务系统内部�?    外部查询都依赖的主状态对象�?    """

    task_id: str
    status: TaskStatus = 'queued'
    request: TaskRequest
    task_spec: TaskSpec | None = None
    task_run: TaskRun | None = None
    plan: TaskPlan | None = None
    plan_version: int = 1
    current_step: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    focus_aspects: list[str] = Field(default_factory=list)
    evidence_pack_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    final_artifact_id: str | None = None
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)
    failures: list[TaskFailure] = Field(default_factory=list)
    plan_revisions: list[PlanRevision] = Field(default_factory=list)
    task_memory_entries: list[TaskMemoryEntry] = Field(default_factory=list)
    artifact_memory_entries: list[ArtifactMemoryEntry] = Field(default_factory=list)
    reflection_entries: list[ReflectionEntry] = Field(default_factory=list)
    tool_call_history: list[ToolCallRecord] = Field(default_factory=list)
    sub_agent_runs: list[SubAgentRunRecord] = Field(default_factory=list)
    run_events: list[TaskRunEvent] = Field(default_factory=list)
    context_bundles: dict[str, ContextBundle] = Field(default_factory=dict)
    memory_records: list[MemoryRecord] = Field(default_factory=list)
    prompt_specs: list[PromptSpec] = Field(default_factory=list)
    prompt_build_requests: list[PromptBuildRequest] = Field(default_factory=list)
    prompt_build_results: list[PromptBuildResult] = Field(default_factory=list)
    grounded_context: GroundedContext | None = None
    graph_subgraph: GraphSubgraph | None = None
    retrieval_quality_report: RetrievalQualityReport | None = None
    result_contract: ResultContract | None = None
    evaluation_scorecard: TaskEvaluationScorecard | None = None
    regression_result: TaskRegressionResult | None = None
    retry_count: int = 0
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    claimed_by: str | None = None
    created_at: datetime
    updated_at: datetime
    final_artifact: Artifact | None = None

    def ensure_runtime_contracts(self) -> None:
        """确保任务具备平台�?`TaskSpec` / `TaskRun` 契约�?
        这是任务详情�?workflow 之间的关键桥接点：不管任务最初来�?API、持久化恢复还是测试构造，
        在真正进�?workflow 前都需要通过这里补齐统一运行态�?        """
        runtime_task_spec = self._build_runtime_task_spec()
        if self.task_spec is None:
            self.task_spec = runtime_task_spec
        else:
            self.task_spec.task_type = runtime_task_spec.task_type
            self.task_spec.objective = runtime_task_spec.objective
            self.task_spec.input_payload = dict(runtime_task_spec.input_payload)
            self.task_spec.run_budget = runtime_task_spec.run_budget
            self.task_spec.steps = list(runtime_task_spec.steps)
            self.task_spec.success_criteria = list(runtime_task_spec.success_criteria)

        if self.task_run is None:
            self.task_run = TaskRun(
                run_id=self._build_task_run_id(),
                task_id=self.task_id,
                status=self.status,
                current_step_id=self.current_step,
                completed_step_ids=list(self.completed_steps),
                budget=self.request.to_run_budget(),
                step_specs=list(self.task_spec.steps if self.task_spec is not None else []),
                started_at=self.started_at,
                completed_at=self.completed_at,
            )
        else:
            self.task_run.budget = self.request.to_run_budget()
            self.task_run.step_specs = list(self.task_spec.steps if self.task_spec is not None else [])
            self.task_run.current_step_id = self.current_step
            self.task_run.completed_step_ids = list(self.completed_steps)
            self.task_run.status = self.status
            self.task_run.started_at = self.started_at
            self.task_run.completed_at = self.completed_at

    def _build_runtime_task_spec(self) -> TaskSpec:
        """按任务类型构建统一 runtime `TaskSpec`�?
        这里会把请求�?task_type 路由到对�?skill adapter；若任务已有 planner 产出�?exit criteria�?        也会同步覆盖到平台层成功条件�?        """
        from app.workflows.tasks.document_analysis_task_adapter import build_task_spec_for_request

        task_spec = build_task_spec_for_request(self.request)
        if self.plan is not None and self.plan.exit_criteria:
            task_spec.success_criteria = list(self.plan.exit_criteria)
        return task_spec

    def _build_task_run_id(self) -> str:
        """为当前任务生成稳定的 attempt run id�?
        `retry_count + 1` 的命名方式保证同一�?task 在多次尝试间拥有可预测的 run 身份�?        """
        return f'run-{self.task_id}-attempt-{self.retry_count + 1}'


class TaskRunSummary(BaseModel):
    """task runtime 列表项�?
    用于任务运行历史列表展示，强调可筛选、可概览的字段，不追求完整执行上下文�?    """

    run_id: str
    task_id: str
    status: str
    task_type: str
    collection_name: str
    instructions: str
    created_at: datetime
    completed_at: datetime | None = None
    checkpoint_count: int = 0
    event_count: int = 0
    replayed_from_checkpoint_id: str | None = None
    last_checkpoint_id: str | None = None
    final_artifact_id: str | None = None
    latency_ms: int | None = None
    recoverable: bool = False


class TaskRunDetail(TaskRunSummary):
    """task runtime 详情�?
    �?summary 基础上补�?checkpoint、上下文包、prompt 构建记录和结果契约，用于恢复、排障和审计�?    """

    request_payload: dict[str, Any]
    task_spec: TaskSpec
    task_run: TaskRun
    checkpoints: list[CheckpointRecord]
    run_events: list[TaskRunEvent]
    context_bundles: dict[str, ContextBundle]
    memory_records: list[MemoryRecord] = Field(default_factory=list)
    prompt_specs: list[PromptSpec] = Field(default_factory=list)
    prompt_build_requests: list[PromptBuildRequest] = Field(default_factory=list)
    prompt_build_results: list[PromptBuildResult] = Field(default_factory=list)
    grounded_context: GroundedContext | None = None
    graph_subgraph: GraphSubgraph | None = None
    retrieval_quality_report: RetrievalQualityReport | None = None
    result_contract: ResultContract | None = None
    final_artifact_id: str | None = None


class TaskRunReplayRequest(BaseModel):
    """指定从哪�?checkpoint 重放 task runtime�?""

    checkpoint_id: str | None = None


class TaskSummaryItem(BaseModel):
    """任务列表项�?
    �?`TaskRunSummary` 不同，这里聚焦的是任务主对象，而不是某一次运行尝试�?    """

    task_id: str
    task_type: str
    collection_name: str
    status: TaskStatus
    final_artifact_id: str | None = None
    retry_count: int = 0
    claimed_by: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    """任务列表响应�?
    对列表数据和分页元信息做统一封装，供 API 层稳定返回�?    """

    items: list[TaskSummaryItem]
    total: int
    limit: int
    offset: int

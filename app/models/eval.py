"""评测模型模块。

负责定义 Ragas 评测、策略对比、查询回放对比，以及 Document Analysis benchmark
相关的数据模型，供评测 API、评测服务和报告读取逻辑共同复用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RagasEvalRequest(BaseModel):
    """发起单个 Ragas 评测任务的请求体。

    把一次评测要用的数据集路径和检索策略开关收在一起。
    这里的布尔开关基本一一对应 query 链路中的检索增强能力，方便把线上策略直接搬到离线评测。
    """

    # 数据集与目标集合。
    dataset_path: str
    collection_name: str
    top_k: int = Field(default=5, ge=1)

    # 检索增强与改写策略开关。
    use_query_rewrite: bool = True
    use_multi_query: bool = False
    multi_query_count: int = Field(default=3, ge=2, le=6)
    use_multi_rewrite: bool = False
    multi_rewrite_count: int = Field(default=3, ge=2, le=6)
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    use_hyde: bool = False
    use_long_context_reorder: bool = False
    use_parent_chunk_retrieval: bool = False
    use_question_oriented_index: bool = False
    use_corrective_rag: bool = False
    use_graph_rag: bool = False
    graph_max_hops: int = Field(default=1, ge=1, le=3)
    graph_top_k: int = Field(default=5, ge=1, le=20)
    graph_entity_types: list[str] | None = None


class EvalStrategyConfig(BaseModel):
    """描述一次对比评测中的单个检索策略。

    用来表达“同一份数据集下，不同检索组合怎么跑”。
    它既能内嵌在对比请求中，也能在结果里原样回显，保证策略定义前后一致。
    """

    name: str
    top_k: int | None = Field(default=None, ge=1)
    use_query_rewrite: bool = True
    use_multi_query: bool = False
    multi_query_count: int = Field(default=3, ge=2, le=6)
    use_multi_rewrite: bool = False
    multi_rewrite_count: int = Field(default=3, ge=2, le=6)
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    use_hyde: bool = False
    use_long_context_reorder: bool = False
    use_parent_chunk_retrieval: bool = False
    use_question_oriented_index: bool = False
    use_corrective_rag: bool = False
    use_graph_rag: bool = False
    graph_max_hops: int = Field(default=1, ge=1, le=3)
    graph_top_k: int | None = Field(default=None, ge=1, le=20)
    graph_entity_types: list[str] | None = None


class RagasCompareRequest(BaseModel):
    """发起多策略评测对比时的请求体。

    与单策略评测相比，这里强调的是“同一数据集下多种检索组合的横向比较”。
    """

    dataset_path: str
    collection_name: str
    strategies: list[EvalStrategyConfig] = Field(min_length=2)
    baseline_name: str | None = None


class RagasCompareMetricItem(BaseModel):
    """单项指标在策略对比中的汇总结果。

    用于说明基线值、最佳策略和值，以及各策略相对基线的差异。
    """

    metric: str
    baseline: float | None = None
    best_strategy: str | None = None
    best_value: float | None = None
    deltas: dict[str, float] = Field(default_factory=dict)


class RagasCompareStrategyResult(BaseModel):
    """某一策略对应的评测任务结果。"""

    strategy: EvalStrategyConfig
    task: 'EvalTaskResponse'


class RagasCompareResponse(BaseModel):
    """多策略评测对比的汇总响应。

    适合直接给前端或报告生成逻辑做结果展示。
    该模型是多策略对比的主输出，汇总了策略列表、指标比较和最终结果文件位置。
    """

    compare_id: str
    dataset_path: str
    collection_name: str
    baseline_name: str
    summary: str
    strategies: list[RagasCompareStrategyResult]
    metrics: dict[str, RagasCompareMetricItem] = Field(default_factory=dict)
    result_path: str | None = None
    completed_at: datetime


class EvalTaskResponse(BaseModel):
    """评测任务的状态与结果摘要。

    该模型被多种评测入口复用，作为“异步评测任务当前跑到哪一步了”的统一结果面。
    无论任务来自 Ragas、回放对比还是反馈评测，最终都尽量投影成这一种状态模型。
    """

    task_id: str
    status: str
    summary: str | None = None
    dataset_path: str | None = None
    collection_name: str | None = None
    sample_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    result_path: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ReplayCompareRequest(BaseModel):
    """发起不依赖 RAGAS 的回放对比请求，用于离线回归。

    这类对比更偏工程回归验证，而不是依赖外部评测指标框架。
    """

    dataset_path: str
    collection_name: str
    strategies: list[EvalStrategyConfig] = Field(min_length=2)
    baseline_name: str | None = None


class ReplayBucketStats(BaseModel):
    """某一 bucket 在单个策略下的回放统计。

    bucket 常用于区分题型、数据域或难度层次，便于快速识别退化集中在哪一类样本。
    """

    bucket: str
    sample_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    avg_retrieved_count: float = 0.0
    avg_latency_ms: float = 0.0


class ReplayStrategySummary(BaseModel):
    """某个策略的回放汇总。

    除总体统计外，还可以细分到 bucket 视角，便于发现某类样本上的局部退化。
    """

    strategy: EvalStrategyConfig
    sample_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    avg_retrieved_count: float = 0.0
    avg_latency_ms: float = 0.0
    buckets: dict[str, ReplayBucketStats] = Field(default_factory=dict)


class ReplayCompareResponse(BaseModel):
    """回放对比结果。

    用于承载离线回放对比的总体结论、分 bucket 指标和结果文件路径。
    """

    compare_id: str
    dataset_path: str
    collection_name: str
    baseline_name: str
    summary: str
    strategies: list[ReplayStrategySummary]
    metrics: dict[str, 'ReplayCompareMetricItem'] = Field(default_factory=dict)
    bucket_metrics: dict[str, dict[str, 'ReplayCompareMetricItem']] = Field(default_factory=dict)
    result_path: str | None = None
    completed_at: datetime


class ReplayCompareMetricItem(BaseModel):
    """回放对比指标的汇总结果。

    与 Ragas 对比指标保持类似结构，方便前端与报告逻辑复用一套渲染方式。
    """

    metric: str
    baseline: float | None = None
    best_strategy: str | None = None
    best_value: float | None = None
    deltas: dict[str, float] = Field(default_factory=dict)


class DocumentAnalysisBenchmarkRequest(BaseModel):
    """发起 Document Analysis Agent 基准回归的请求体。

    主要用来约束门禁阈值、轮询节奏和等待时间。
    """

    dataset_path: str
    collection_name: str | None = None
    poll_interval_seconds: float = Field(default=0.2, ge=0.05, le=5.0)
    max_wait_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    min_success_rate: float = Field(default=0.9, ge=0.0, le=1.0)
    min_avg_score: float = Field(default=0.7, ge=0.0, le=1.0)
    max_unsupported_claim_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_review_replan_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    max_p95_latency_ms: float = Field(default=30000.0, ge=0.0)


class DocumentAnalysisBenchmarkSample(BaseModel):
    """单条 benchmark 样本的执行与评分结果。

    这里既保存任务执行结果，也保存评分、证据覆盖和排障指标。
    """

    index: int
    instructions: str
    collection_name: str
    bucket: str = 'default'
    doc_ids: list[str] = Field(default_factory=list)
    focus_dimensions: list[str] = Field(default_factory=list)
    key_evidence_points: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    task_id: str | None = None
    status: str
    score: float = 0.0
    findings_hit_rate: float = 0.0
    risks_hit_rate: float = 0.0
    focus_dimension_hit_rate: float = 0.0
    key_evidence_hit_rate: float = 0.0
    artifact_completeness: float = 0.0
    evidence_coverage: float = 0.0
    grounded_finding_ratio: float = 0.0
    grounded_risk_ratio: float = 0.0
    evidence_usability_score: float = 0.0
    unsupported_claim_count: int = 0
    plan_version: int = 1
    artifact_version_count: int = 0
    review_passed: bool = True
    review_replan_count: int = 0
    step_count: int = 0
    tool_calls: int = 0
    tool_error_count: int = 0
    sub_agent_run_count: int = 0
    sub_agent_failure_count: int = 0
    latency_ms: int = 0
    estimated_cost_units: float = 0.0
    evidence_count: int = 0
    evidence_gap_count: int = 0
    open_question_count: int = 0
    review_note_count: int = 0
    retrieval_mode: str | None = None
    rerank_mode: str | None = None
    retrieval_candidate_count: int = 0
    retrieval_selected_count: int = 0
    step_trace: dict[str, int] = Field(default_factory=dict)
    tool_trace: dict[str, dict[str, float]] = Field(default_factory=dict)
    sub_agent_trace: dict[str, dict[str, float | int | dict[str, int]]] = Field(default_factory=dict)
    artifact_trace: dict[str, float | bool] = Field(default_factory=dict)
    expected_findings: list[str] = Field(default_factory=list)
    expected_risks: list[str] = Field(default_factory=list)
    forbidden_claim_hit_count: int = 0
    error: str | None = None


class DocumentAnalysisDashboardSummary(BaseModel):
    """供 dashboard / 趋势脚本直接消费的 benchmark 聚合摘要。

    它是 benchmark 结果向可视化层投影后的汇总面，强调趋势、拆解和最差样本定位能力。
    """

    benchmark_id: str
    collection_name: str | None = None
    sample_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    success_rate: float = 0.0
    avg_score: float = 0.0
    avg_latency_ms: float = 0.0
    avg_tool_calls: float = 0.0
    avg_step_count: float = 0.0
    avg_evidence_count: float = 0.0
    avg_open_question_count: float = 0.0
    avg_focus_dimension_hit_rate: float = 0.0
    avg_key_evidence_hit_rate: float = 0.0
    avg_evidence_coverage: float = 0.0
    avg_grounded_finding_ratio: float = 0.0
    avg_grounded_risk_ratio: float = 0.0
    avg_evidence_usability_score: float = 0.0
    unsupported_claim_rate: float = 0.0
    review_pass_rate: float = 0.0
    review_replan_rate: float = 0.0
    avg_plan_version: float = 0.0
    avg_artifact_versions: float = 0.0
    avg_tool_error_count: float = 0.0
    avg_sub_agent_run_count: float = 0.0
    avg_sub_agent_failure_count: float = 0.0
    avg_retrieval_candidate_count: float = 0.0
    avg_retrieval_selected_count: float = 0.0
    avg_evidence_gap_count: float = 0.0
    avg_review_note_count: float = 0.0
    total_estimated_cost_units: float = 0.0
    avg_estimated_cost_units: float = 0.0
    step_breakdown: dict[str, float] = Field(default_factory=dict)
    tool_breakdown: dict[str, dict[str, float]] = Field(default_factory=dict)
    sub_agent_breakdown: dict[str, dict[str, float | int | dict[str, int]]] = Field(default_factory=dict)
    retrieval_mode_breakdown: dict[str, int] = Field(default_factory=dict)
    rerank_mode_breakdown: dict[str, int] = Field(default_factory=dict)
    artifact_status_breakdown: dict[str, float] = Field(default_factory=dict)
    bucket_breakdown: dict[str, 'DocumentAnalysisDashboardSliceSummary'] = Field(default_factory=dict)
    collection_breakdown: dict[str, 'DocumentAnalysisDashboardSliceSummary'] = Field(default_factory=dict)
    worst_samples: list['DocumentAnalysisDashboardWorstSample'] = Field(default_factory=list)


class DocumentAnalysisDashboardSliceSummary(BaseModel):
    """dashboard 中按 bucket/collection 聚合后的切片摘要。

    该模型把总体 dashboard 指标切到某个局部维度，便于查看特定 bucket 或集合是否拖后腿。
    """

    label: str
    sample_count: int = 0
    success_rate: float = 0.0
    avg_score: float = 0.0
    avg_evidence_coverage: float = 0.0
    avg_evidence_usability_score: float = 0.0
    avg_latency_ms: float = 0.0
    avg_tool_error_count: float = 0.0


class DocumentAnalysisDashboardWorstSample(BaseModel):
    """dashboard 中用于快速排查的低分或失败样本。

    这部分通常会直接展示在报告顶部，帮助研发先看最值得排查的异常样本。
    """

    index: int
    task_id: str | None = None
    bucket: str = 'default'
    collection_name: str | None = None
    status: str = 'unknown'
    score: float = 0.0
    evidence_coverage: float = 0.0
    evidence_usability_score: float = 0.0
    unsupported_claim_count: int = 0
    review_replan_count: int = 0
    error: str | None = None


class DocumentAnalysisBenchmarkGate(BaseModel):
    """任务级 benchmark 的门禁结果。

    用于把一组阈值检查结果收敛成最终门禁状态、建议和原因列表。
    """

    status: str = 'unknown'
    recommendation: str = '-'
    reasons: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)


class DocumentAnalysisBenchmarkResponse(BaseModel):
    """Document Analysis Agent 基准回归结果。

    这是单次 benchmark 的主结果模型，既包含样本明细，也包含汇总指标、dashboard 摘要和门禁结论。
    """

    benchmark_id: str
    dataset_path: str
    collection_name: str | None = None
    summary: str
    sample_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    samples: list[DocumentAnalysisBenchmarkSample] = Field(default_factory=list)
    dashboard_summary: DocumentAnalysisDashboardSummary | None = None
    gate: DocumentAnalysisBenchmarkGate | None = None
    result_path: str | None = None
    completed_at: datetime


class DocumentAnalysisBenchmarkReportSummary(BaseModel):
    """单次 benchmark 报告的列表摘要。

    适用于历史列表、报告目录页和趋势分析的输入数据。
    """

    benchmark_id: str
    collection_name: str | None = None
    completed_at: str | None = None
    gate_status: str = 'unknown'
    result_path: str
    sample_count: int = 0
    success_rate: float = 0.0
    avg_score: float = 0.0
    avg_evidence_coverage: float = 0.0
    avg_evidence_usability_score: float = 0.0


class DocumentAnalysisBenchmarkHistoryResponse(BaseModel):
    """benchmark 报告历史列表。

    统一封装历史报告项和分页信息，便于做趋势页或报告检索页。
    """

    items: list[DocumentAnalysisBenchmarkReportSummary] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class DocumentAnalysisBenchmarkReportResponse(BaseModel):
    """单次 benchmark 报告完整响应。

    把报告文件路径、dashboard 摘要、门禁结论和完整 benchmark 结果打包在一起。
    """

    report_path: str
    report_mode: str = 'document_analysis_benchmark'
    dashboard_summary: DocumentAnalysisDashboardSummary | None = None
    gate: DocumentAnalysisBenchmarkGate | None = None
    result: DocumentAnalysisBenchmarkResponse


class DocumentAnalysisBaselineCandidate(BaseModel):
    """单个 baseline 候选项。

    用于统一表示来自 benchmark、报告、历史任务或版本记录的候选基线。
    """

    kind: str
    reference: str
    task_id: str | None = None
    collection_name: str | None = None
    policy_name: str | None = None
    policy_version: str | None = None
    scorecard_version: str | None = None
    overall_score: float = 0.0
    coverage_score: float = 0.0
    grounding_score: float = 0.0
    review_score: float = 0.0
    unsupported_claim_rate: float = 0.0
    generated_at: datetime | None = None
    managed_entry_id: str | None = None
    managed_status: str | None = None


class DocumentAnalysisBaselineRegistryItem(DocumentAnalysisBaselineCandidate):
    """baseline 注册表中的单项记录。

    在候选信息之外补充选中状态与排序信息，便于前端展示“当前基线链路是如何决策出来的”。
    """

    selected: bool = False
    order_index: int | None = None


class DocumentAnalysisBaselineResolutionResponse(BaseModel):
    """统一 baseline 解析结果。

    用于回答“本次任务最终选中了哪个 baseline、候选还有哪些、为什么按这个顺序解析”。
    """

    collection_name: str
    instructions: str
    policy_name: str
    policy_version: str
    baseline_order: list[str] = Field(default_factory=list)
    selected_baseline: DocumentAnalysisBaselineCandidate | None = None
    candidates: list[DocumentAnalysisBaselineCandidate] = Field(default_factory=list)


class DocumentAnalysisBaselineRegistryResponse(BaseModel):
    """baseline 注册表列表响应。

    该模型既承载注册表分页结果，也保留当前解析上下文和已选基线，便于一个接口完成列表与决策解释。
    """

    collection_name: str
    instructions: str
    policy_name: str
    policy_version: str
    baseline_order: list[str] = Field(default_factory=list)
    kind: str | None = None
    total: int = 0
    limit: int = 20
    offset: int = 0
    kind_counts: dict[str, int] = Field(default_factory=dict)
    selected_baseline: DocumentAnalysisBaselineCandidate | None = None
    items: list[DocumentAnalysisBaselineRegistryItem] = Field(default_factory=list)


class ManagedDocumentAnalysisBaselineEntry(DocumentAnalysisBaselineCandidate):
    """持久化到本地注册表的 baseline 记录。

    在候选基线的基础上，再补充组织、审核、状态和维护人信息，形成可治理的数据实体。
    """

    entry_id: str
    organization_id: str | None = None
    tenant_id: str | None = None
    binding_policy_name: str | None = None
    binding_instruction_substring: str | None = None
    status: Literal['draft', 'active', 'archived'] = 'active'
    review_status: Literal['pending', 'approved', 'rejected'] = 'approved'
    review_note: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class ManagedDocumentAnalysisBaselineListResponse(BaseModel):
    """已注册 baseline 的列表响应。

    用于基线治理后台的分页查询，支持按状态、集合和评审信息进行筛选。
    """

    total: int = 0
    limit: int = 20
    offset: int = 0
    kind: str | None = None
    status: str | None = None
    collection_name: str | None = None
    binding_policy_name: str | None = None
    binding_instruction_substring: str | None = None
    review_status: str | None = None
    items: list[ManagedDocumentAnalysisBaselineEntry] = Field(default_factory=list)


class ManagedDocumentAnalysisBaselineRegisterRequest(BaseModel):
    """显式注册 baseline 到本地注册表。

    该请求把基线来源、绑定条件、审核状态和操作者信息一次性固化，适合治理场景下的显式登记。
    """

    collection_name: str = Field(min_length=1)
    instructions: str | None = None
    organization_id: str | None = None
    tenant_id: str | None = None
    actor: str | None = None
    actor_role: str | None = None
    binding_instruction_substring: str | None = None
    output_format: Literal['markdown', 'json', 'markdown+json'] = 'markdown'
    kind: Literal['benchmark', 'report', 'version', 'task']
    reference: str = Field(min_length=1)
    status: Literal['draft', 'active', 'archived'] = 'active'
    review_status: Literal['pending', 'approved', 'rejected'] = 'approved'
    review_note: str | None = None
    note: str | None = None


class ManagedDocumentAnalysisBaselineUpdateRequest(BaseModel):
    """更新已注册 baseline 的状态或备注。

    采用局部可选字段设计，便于审核、归档和维护备注等动作按需更新。
    """

    status: Literal['draft', 'active', 'archived'] | None = None
    actor: str | None = None
    actor_role: str | None = None
    binding_instruction_substring: str | None = None
    review_status: Literal['pending', 'approved', 'rejected'] | None = None
    review_note: str | None = None
    note: str | None = None


class ManagedDocumentAnalysisBaselineAuditEntry(BaseModel):
    """managed baseline 注册表的审计事件。

    用于追踪注册、更新、归档等管理动作，保证基线治理过程可追溯。
    """

    audit_id: str
    entry_id: str
    action: Literal['created', 'updated', 'deleted', 'archived']
    actor: str | None = None
    actor_role: str | None = None
    summary: str
    snapshot: ManagedDocumentAnalysisBaselineEntry | None = None
    created_at: datetime


class ManagedDocumentAnalysisBaselineAuditListResponse(BaseModel):
    """managed baseline 审计日志列表。

    用于回放某个基线实体的变更历史，支撑治理审计与责任追踪。
    """

    total: int = 0
    limit: int = 20
    offset: int = 0
    entry_id: str | None = None
    items: list[ManagedDocumentAnalysisBaselineAuditEntry] = Field(default_factory=list)


class DocumentAnalysisTrendMetricItem(BaseModel):
    """趋势窗口中的单个任务指标变化。

    记录某项核心指标从窗口起点到最新报告的变化量，便于快速判断趋势方向。
    """

    metric: str
    first_value: float = 0.0
    latest_value: float = 0.0
    delta: float = 0.0


class DocumentAnalysisTrendToolItem(BaseModel):
    """趋势窗口中最新一次工具指标。

    聚焦最新窗口末端的工具质量信号，通常用于发现某个工具近期是否退化。
    """

    tool_name: str
    latest_error_rate: float = 0.0
    latest_avg_duration_ms: float = 0.0


class DocumentAnalysisTrendSubAgentItem(BaseModel):
    """趋势窗口中最新一次子代理指标。

    用于观察子代理运行频次和失败率在最近报告中的表现。
    """

    agent_name: str
    latest_run_count: float = 0.0
    latest_failure_rate: float = 0.0


class DocumentAnalysisTrendGateItem(BaseModel):
    """趋势窗口中的门禁历史项。

    每一项对应一次 benchmark 的门禁结论，方便按时间轴查看通过/阻断变化。
    """

    completed_at: str = ''
    benchmark_id: str = '-'
    gate_status: str = 'unknown'
    recommendation: str = '-'


class DocumentAnalysisTrendResponse(BaseModel):
    """Document Analysis benchmark 趋势查询响应。

    该模型聚焦跨多份 benchmark 报告的时间维度变化，供趋势页或巡检脚本直接消费。
    """

    generated_at: str
    report_count: int = 0
    latest_report_path: str
    latest_completed_at: str = ''
    latest_benchmark_id: str = '-'
    latest_collection_name: str = '-'
    insights: list[str] = Field(default_factory=list)
    gate_counts: dict[str, int] = Field(default_factory=dict)
    gate_history: list[DocumentAnalysisTrendGateItem] = Field(default_factory=list)
    metric_trends: list[DocumentAnalysisTrendMetricItem] = Field(default_factory=list)
    tool_trends: list[DocumentAnalysisTrendToolItem] = Field(default_factory=list)
    sub_agent_trends: list[DocumentAnalysisTrendSubAgentItem] = Field(default_factory=list)
    latest_dashboard_summary: DocumentAnalysisDashboardSummary | None = None
    latest_gate: DocumentAnalysisBenchmarkGate | None = None


ReplayCompareResponse.model_rebuild()
RagasCompareStrategyResult.model_rebuild()

"""文档分析任务状态模块。

负责定义文档分析工作流在节点间共享的状态结构和初始化逻辑。该模块位于任务 workflow
基础层，用统一字段约定串联计划分发、证据检索、草稿生成、审查修订和最终交付。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, TypedDict

try:
    from typing import NotRequired, Required
except ImportError:  # pragma: no cover - Python < 3.11
    from typing_extensions import NotRequired, Required

from app.harness.grounding import GroundingBundle
from app.harness.models import ContextBundle
from app.models.artifact import EvidencePack, ReportArtifactContent, ReviewResult, RiskItem
from app.models.task import CheckpointRecord, StepRuntimeRecord, TaskDetail, TaskPlan, TaskResult


class DocumentAnalysisState(TypedDict, total=False):
    """描述文档分析任务在节点间传递的共享状态。

    该状态同时承载任务对象、计划执行进度、证据包、分析结果、草稿与审查结果，以及退出条件
    判断所需的中间字段，保证任务图中的各节点能够以统一协议交换执行上下文。
    """

    task: Required[TaskDetail]
    skill_name: str
    artifact_type: str
    artifact_title: str
    started_at: Required[float]
    plan: TaskPlan | None
    focus_aspects: list[str]
    pending_plan_step_ids: list[str]
    completed_plan_step_ids: list[str]
    active_plan_step_id: str | None
    document_context: dict[str, Any]
    evidence_pack: EvidencePack | None
    analysis: dict[str, Any]
    risks: list[RiskItem]
    grounding_result: GroundingBundle | None
    draft_content: ReportArtifactContent | None
    draft_artifact_id: str | None
    review: ReviewResult | None
    result: TaskResult | None
    error: str | None
    current_step_id: str | None
    completed_step_ids: list[str]
    step_runtimes: dict[str, StepRuntimeRecord]
    checkpoints: list[CheckpointRecord]
    run_events: list[dict[str, Any]]
    context_bundles: dict[str, ContextBundle]
    replayed_from_checkpoint_id: str | None
    graph_entry_route: str | None
    revise_count: int
    tool_call_count: int
    exit_criteria_failures: list[str]
    exit_decision: str | None
    final_artifact_id: NotRequired[str | None]


# LangGraph 节点更新统一使用松散字典，具体字段约束由 `DocumentAnalysisState` 和节点契约保证。
DocumentAnalysisUpdate = dict[str, Any]


def init_document_analysis_state(task: TaskDetail) -> DocumentAnalysisState:
    """初始化一次文档分析任务状态。

    Args:
        task: 待执行的任务详情对象。

    Returns:
        已填充基础字段的初始工作流状态字典。
    """

    return {
        'task': task,
        'started_at': perf_counter(),
        'plan': task.plan,
        'pending_plan_step_ids': [],
        'completed_plan_step_ids': [],
        'active_plan_step_id': None,
        'evidence_pack': None,
        'draft_content': None,
        'review': None,
        'result': None,
        'error': None,
        # 下面三组字段分别服务于步骤生命周期跟踪、可恢复 checkpoint 和 route runtime 观测。
        'current_step_id': task.current_step,
        'completed_step_ids': list(task.completed_steps),
        'step_runtimes': dict(task.task_run.step_runtimes if task.task_run is not None else {}),
        'checkpoints': list(task.task_run.checkpoints if task.task_run is not None else []),
        'run_events': [item.model_dump(mode='python') for item in task.run_events],
        'context_bundles': dict(task.context_bundles),
        'replayed_from_checkpoint_id': None,
        'graph_entry_route': None,
        'revise_count': 0,
        'tool_call_count': 0,
        'exit_criteria_failures': [],
        'exit_decision': None,
    }

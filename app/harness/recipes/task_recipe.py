"""Task 工作流的 Recipe 定义。

以声明式 Recipe + Stage 方式组织文档分析等任务的执行流程。
"""

from __future__ import annotations

from app.harness.core.recipe import BaseRecipe
from app.harness.core.stage import BaseStage


class PlanStage(BaseStage):
    """任务计划阶段。"""

    name = 'plan'
    description = '根据任务请求生成执行计划'

    def run(self, state: dict, ctx) -> dict:
        return state


class CollectDocumentContextStage(BaseStage):
    """文档上下文收集阶段。"""

    name = 'collect_document_context'
    description = '加载文档上下文信息'
    allowed_tools = ['rag_load_document_context']
    requires_policy_check = False

    def run(self, state: dict, ctx) -> dict:
        return state


class RetrieveEvidenceStage(BaseStage):
    """证据检索阶段。"""

    name = 'retrieve_evidence'
    description = '执行混合及图谱检索，收集证据'
    allowed_tools = ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence']

    def run(self, state: dict, ctx) -> dict:
        return state


class AnalyzeStage(BaseStage):
    """分析阶段。"""

    name = 'analyze'
    description = '基于检索到的证据执行结构化分析'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        return state


class DraftReportStage(BaseStage):
    """报告草稿生成阶段。"""

    name = 'draft_report'
    description = '基于分析结果生成报告草稿'
    allowed_tools = ['draft_report']
    risk_level = 'medium'

    def run(self, state: dict, ctx) -> dict:
        return state


class ReviewStage(BaseStage):
    """审查阶段。"""

    name = 'review'
    description = '对报告草稿做质量审查'
    allowed_tools = ['review_report']

    def run(self, state: dict, ctx) -> dict:
        return state


class ReviseStage(BaseStage):
    """修订阶段。"""

    name = 'revise'
    description = '根据审查意见修订报告'
    allowed_tools = ['draft_report']

    def run(self, state: dict, ctx) -> dict:
        return state


class FinalizeReportStage(BaseStage):
    """报告最终确认阶段。"""

    name = 'finalize_report'
    description = '最终确认并输出报告'
    allowed_tools = ['finalize_report']
    risk_level = 'high'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        return state


class DocumentAnalysisRecipe(BaseRecipe):
    """文档分析任务 recipe。"""

    name = 'document_analysis'
    description = '文档分析工作流：计划 → 检索 → 分析 → 起草 → 审查 → 修订 → 确认'
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
    """文档摘要任务 recipe。"""

    name = 'document_summary'
    description = '文档摘要工作流：检索 → 摘要 → 输出'
    task_type = 'document_summary'
    version = 'v1'

    def __init__(self) -> None:
        super().__init__(stages=[
            CollectDocumentContextStage(),
            AnalyzeStage(),
            FinalizeReportStage(),
        ])
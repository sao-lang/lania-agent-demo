"""任务产物模型模块。

负责定义文档分析任务中的证据包、关键发现、风险项、审查结果和最终报告产物结构，作为任务
工作流、存储层和 API 层之间共享的数据契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """描述一条可追溯的证据引用。

    用于把最终报告中的结论与底层检索片段关联起来，保证任务产物具备可审计性。
    """

    citation_id: str
    source: str
    chunk_id: str
    text: str
    support_score: float = Field(default=0.0, ge=0.0, le=1.0)
    page: int | None = None
    tags: list[str] = Field(default_factory=list)


class EvidencePack(BaseModel):
    """聚合一次任务分析所使用的证据包。

    该模型通常作为中间态输出，帮助后续起草、审查和重规划节点共享证据覆盖情况。
    """

    task_id: str
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_aspects: list[str] = Field(default_factory=list)


class FindingItem(BaseModel):
    """结构化关键发现。

    用于把报告中的核心结论拆成可检索、可引用、可追踪的最小条目。
    """

    finding_id: str
    title: str
    summary: str
    citation_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RiskItem(BaseModel):
    """结构化风险项。

    与关键发现并列，专门表达潜在风险、严重度和可选建议，便于后续审查或自动提取。
    """

    risk_id: str
    title: str
    description: str
    severity: Literal['low', 'medium', 'high'] = 'medium'
    citation_ids: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class ReviewResult(BaseModel):
    """报告审查结果。

    用于描述草稿在证据支撑和章节完整性上的审查反馈。
    """

    passed: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)


class ReportArtifactContent(BaseModel):
    """文档分析任务最终交付的结构化内容。

    同时兼容 Markdown 展示和 JSON 结构化消费场景。
    """

    title: str | None = None
    summary: str
    key_findings: list[FindingItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    report_markdown: str | None = None
    report_json: dict[str, Any] | None = None


class Artifact(BaseModel):
    """统一的任务产物模型。

    该模型承载任务产物的版本、状态、内容和可选审查结果，是任务系统对外暴露的核心交付对象。
    """

    artifact_id: str
    task_id: str
    artifact_type: str
    version: int = Field(default=1, ge=1)
    status: Literal['draft', 'final'] = 'draft'
    content: ReportArtifactContent
    review: ReviewResult | None = None
    created_at: datetime
    updated_at: datetime

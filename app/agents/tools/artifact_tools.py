"""报告产物工具模块。

负责把分析结果整理成报告草稿、做结构审查，并在最后补齐 markdown/json 两种产物格式。
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from app.agents.tools.llm_prompting import build_json_repair_prompt, build_review_report_prompt
from app.agents.tools.base import ToolRetryPolicy
from app.agents.artifacts import ArtifactFormatter
from app.models.artifact import EvidenceItem, FindingItem, ReportArtifactContent, ReviewResult, RiskItem


class DraftReportInput(BaseModel):
    """生成草稿的输入。"""

    summary: str
    key_findings: list[FindingItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class DraftReportOutput(BaseModel):
    """报告草稿输出。"""

    content: ReportArtifactContent


class ReviewReportInput(BaseModel):
    """报告审查输入。"""

    content: ReportArtifactContent


class FinalizeReportInput(BaseModel):
    """最终报告生成输入。"""

    content: ReportArtifactContent
    review: ReviewResult | None = None
    output_format: str = 'markdown+json'


def draft_report_content(payload: DraftReportInput) -> DraftReportOutput:
    """纯函数化的草稿生成逻辑，便于在隔离 worker 中复用。"""
    content = ReportArtifactContent(
        summary=payload.summary,
        key_findings=payload.key_findings,
        risks=payload.risks,
        evidence=payload.evidence,
        open_questions=payload.open_questions,
        confidence=round(max(0.0, min(1.0, payload.confidence)), 2),
    )
    content.report_markdown = ArtifactFormatter.render_markdown(content)
    content.report_json = ArtifactFormatter.render_json(content)
    return DraftReportOutput(content=content)


def review_report_content(payload: ReviewReportInput) -> ReviewResult:
    """纯函数化的报告审查逻辑，便于在隔离 worker 中复用。"""
    content = payload.content
    missing_sections: list[str] = []
    if not content.summary.strip():
        missing_sections.append('summary')
    if not content.key_findings:
        missing_sections.append('key_findings')
    if content.report_markdown is None:
        missing_sections.append('report_markdown')
    if content.report_json is None:
        missing_sections.append('report_json')
    evidence_ids = {item.citation_id for item in content.evidence}
    unsupported_claims: list[str] = []
    for item in content.key_findings:
        if item.citation_ids and not set(item.citation_ids).issubset(evidence_ids):
            unsupported_claims.append(item.finding_id)
    for risk in content.risks:
        if risk.citation_ids and not set(risk.citation_ids).issubset(evidence_ids):
            unsupported_claims.append(risk.risk_id)
    review_notes: list[str] = []
    if content.confidence < 0.5:
        review_notes.append('当前报告置信度偏低，建议补充证据后复核。')
    if content.open_questions:
        review_notes.append(f'仍有 {len(content.open_questions)} 个待确认问题未解决。')
    return ReviewResult(
        passed=not missing_sections and not unsupported_claims,
        unsupported_claims=unsupported_claims,
        missing_sections=missing_sections,
        review_notes=review_notes,
    )


def finalize_report_content(payload: FinalizeReportInput) -> ReportArtifactContent:
    """纯函数化的最终报告收口逻辑，便于在隔离进程中复用。"""
    content = payload.content.model_copy(deep=True)
    if payload.review is not None and payload.review.review_notes:
        notes = '；'.join(payload.review.review_notes)
        content.summary = f'{content.summary} 审查备注：{notes}'.strip()
    if 'markdown' in payload.output_format:
        content.report_markdown = ArtifactFormatter.render_markdown(content)
    if 'json' in payload.output_format:
        content.report_json = ArtifactFormatter.render_json(content)
    return content


class DraftReportTool:
    """生成结构化草稿。"""

    name = 'draft_report'
    version = 'v1'
    timeout_ms = 12000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = DraftReportInput
    output_model = DraftReportOutput

    def run(self, payload: DraftReportInput, context) -> DraftReportOutput:
        """把上游分析结果组装成统一报告内容。"""
        return draft_report_content(payload)


class ReviewReportTool:
    """检查报告结构完整性和证据绑定情况。"""

    name = 'review_report'
    version = 'v1'
    timeout_ms = 15000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ReviewReportInput
    output_model = ReviewResult

    def run(self, payload: ReviewReportInput, context) -> ReviewResult:
        """先做确定性校验，再按需叠加 LLM 审查意见。"""
        fallback = review_report_content(payload)
        llm_review = self._run_llm(payload, context, fallback)
        return llm_review or fallback

    def _run_llm(self, payload: ReviewReportInput, context, fallback: ReviewResult) -> ReviewResult | None:
        """调用 LLM 生成补充审查意见，并与确定性结果合并。"""

        decision = _route_llm(
            context,
            purpose='task_review',
            tool_name=self.name,
            feature_enabled=context.settings.enable_task_llm_review,
            confidence=payload.content.confidence,
        )
        if decision is None:
            return None
        prompt = build_review_report_prompt(payload.content)
        response = _complete_json(prompt, context, self.name, decision=decision, purpose='task_review')
        if response is None:
            return None
        try:
            llm_review = ReviewResult(
                passed=bool(response.get('passed', fallback.passed)),
                unsupported_claims=[str(item) for item in response.get('unsupported_claims', []) if str(item).strip()],
                missing_sections=[str(item) for item in response.get('missing_sections', []) if str(item).strip()],
                review_notes=[str(item) for item in response.get('review_notes', []) if str(item).strip()],
            )
        except Exception:
            return None
        # 以 deterministic 校验结果为底线，避免 LLM 漏检。
        merged_unsupported = sorted(set(fallback.unsupported_claims + llm_review.unsupported_claims))
        merged_sections = sorted(set(fallback.missing_sections + llm_review.missing_sections))
        merged_notes = fallback.review_notes + [item for item in llm_review.review_notes if item not in fallback.review_notes]
        return ReviewResult(
            passed=llm_review.passed and fallback.passed and not merged_unsupported and not merged_sections,
            unsupported_claims=merged_unsupported,
            missing_sections=merged_sections,
            review_notes=merged_notes,
        )


class FinalizeReportTool:
    """在最终输出前补齐 markdown/json。"""

    name = 'finalize_report'
    version = 'v1'
    timeout_ms = 8000
    risk_level = 'high'
    sandbox_mode = 'process_isolated'
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = FinalizeReportInput
    output_model = ReportArtifactContent

    def run(self, payload: FinalizeReportInput, context) -> ReportArtifactContent:
        """按目标格式补齐最终报告内容。"""
        return finalize_report_content(payload)


def _complete_json(prompt: str, context, tool_name: str, *, decision=None, purpose: str | None = None) -> dict | None:
    """让 LLM 返回 JSON；解析失败时再走一次修复提示。"""
    try:
        response = context.llm.complete(prompt)
        raw_text = str(response)
        if decision is not None and getattr(context, 'model_router', None) is not None:
            context.model_router.record_completion(
                context.trace,
                decision,
                prompt_text=prompt,
                response=response,
                response_text=raw_text,
                scope='task_tool',
                task_id=context.task_id,
                tool_name=tool_name,
                step_name=context.step_name,
                purpose=purpose or decision.purpose,
            )
        parsed = _extract_json_object(raw_text)
        if parsed is None:
            repair_decision = _route_llm(context, purpose='json_repair', tool_name=tool_name, feature_enabled=True)
            if repair_decision is None:
                return None
            repair_prompt = build_json_repair_prompt(raw_text, 'strict JSON object')
            repaired = context.llm.complete(repair_prompt)
            repaired_text = str(repaired)
            if getattr(context, 'model_router', None) is not None:
                context.model_router.record_completion(
                    context.trace,
                    repair_decision,
                    prompt_text=repair_prompt,
                    response=repaired,
                    response_text=repaired_text,
                    scope='task_tool',
                    task_id=context.task_id,
                    tool_name=tool_name,
                    step_name=context.step_name,
                    purpose='json_repair',
                )
            parsed = _extract_json_object(repaired_text)
    except Exception as exc:
        context.trace.record(
            'task_tool_llm_fallback',
            {'task_id': context.task_id, 'tool_name': tool_name, 'reason': str(exc)},
        )
        return None
    if parsed is not None:
        context.trace.record(
            'task_tool_llm_used',
            {'task_id': context.task_id, 'tool_name': tool_name, 'mode': 'llm_complete'},
        )
    return parsed


def _extract_json_object(text: str) -> dict | None:
    """从文本里尽量抠出一个 JSON object。"""
    match = re.search(r'\{[\s\S]*\}', text)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _route_llm(context, *, purpose: str, tool_name: str, feature_enabled: bool, confidence: float | None = None):
    """根据路由器决策判断是否启用 LLM 审查分支。"""

    if context.llm is None:
        return None
    router = getattr(context, 'model_router', None)
    if router is None:
        if not feature_enabled:
            return None
        return object()
    decision = router.route(
        purpose=purpose,
        llm_available=context.llm is not None,
        feature_enabled=feature_enabled,
        run_budget=context.run_budget,
        step_name=context.step_name,
    )
    router.record_selection(
        context.trace,
        decision,
        scope='task_tool',
        task_id=context.task_id,
        tool_name=tool_name,
        step_name=context.step_name,
        purpose=purpose,
        **({'content_confidence': confidence} if confidence is not None else {}),
    )
    if decision.mode != 'llm':
        return None
    return decision

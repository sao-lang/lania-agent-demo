"""分析类工具模块。

负责把证据包继续加工成关键发现和风险项，是 Document Analysis Agent 在证据检索之后的主要分析层。
"""

from __future__ import annotations

import json
import re
from typing import Literal, cast

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolRetryPolicy
from app.agents.tools.llm_prompting import (
    build_extract_key_points_prompt,
    build_extract_risks_prompt,
    build_json_repair_prompt,
)
from app.capabilities.knowledge import DocumentContextItem
from app.models.artifact import EvidencePack, FindingItem, RiskItem

SeverityLevel = Literal['low', 'medium', 'high']


class ExtractKeyPointsInput(BaseModel):
    """关键发现提取输入。"""

    instructions: str
    documents: list[DocumentContextItem] = Field(default_factory=list)
    evidence_pack: EvidencePack


class ExtractKeyPointsOutput(BaseModel):
    """关键发现提取输出。"""

    summary: str
    key_findings: list[FindingItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractRisksInput(BaseModel):
    """风险抽取输入。"""

    instructions: str
    evidence_pack: EvidencePack


class ExtractRisksOutput(BaseModel):
    """风险抽取输出。"""

    risks: list[RiskItem] = Field(default_factory=list)


class ExtractKeyPointsTool:
    """基于文档上下文和证据生成结构化发现。"""

    name = 'extract_key_points'
    version = 'v1'
    timeout_ms = 20000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ExtractKeyPointsInput
    output_model = ExtractKeyPointsOutput

    def run(self, payload: ExtractKeyPointsInput, context) -> ExtractKeyPointsOutput:
        """优先走 LLM 生成；走不通时再退回启发式摘要。"""
        llm_output = self._run_llm(payload, context)
        if llm_output is not None:
            return llm_output
        evidence_items = payload.evidence_pack.evidence_items
        findings: list[FindingItem] = []
        for index, item in enumerate(evidence_items[:5], start=1):
            title = self._make_title(item.text, index)
            findings.append(
                FindingItem(
                    finding_id=f'finding-{index}',
                    title=title,
                    summary=self._clip(item.text, 140),
                    citation_ids=[item.citation_id],
                    tags=item.tags[:3],
                )
            )
        doc_titles = '、'.join(doc.title for doc in payload.documents[:3]) or '目标文档'
        summary_bits = [
            f'本次任务围绕 {doc_titles} 展开分析。',
            f'共抽取 {len(findings)} 条关键发现与 {len(evidence_items)} 条证据。',
        ]
        if payload.instructions.strip():
            summary_bits.append(f'分析重点包括：{payload.instructions.strip()}。')
        if payload.evidence_pack.missing_aspects:
            summary_bits.append(f'当前仍缺少对 {"、".join(payload.evidence_pack.missing_aspects[:4])} 的充分证据。')
        open_questions = list(payload.evidence_pack.missing_aspects[:5])
        if not evidence_items:
            findings.append(
                FindingItem(
                    finding_id='finding-gap-1',
                    title='当前证据不足',
                    summary='现有检索结果不足以形成稳定结论，报告以下结论以缺口披露和待确认项为主。',
                    citation_ids=[],
                    tags=['evidence-gap'],
                )
            )
            open_questions.insert(0, '当前检索到的证据不足，建议补充文档或调整查询范围。')
        confidence = self._compute_confidence(payload.evidence_pack.coverage_score, len(evidence_items), len(open_questions))
        return ExtractKeyPointsOutput(
            summary=''.join(summary_bits),
            key_findings=findings,
            open_questions=open_questions,
            confidence=confidence,
        )

    def _run_llm(self, payload: ExtractKeyPointsInput, context) -> ExtractKeyPointsOutput | None:
        """尝试让 LLM 直接产出结构化关键发现。"""
        decision = _route_llm(
            context,
            purpose='task_analysis',
            tool_name=self.name,
            feature_enabled=context.settings.enable_task_llm_analysis,
            evidence_pack=payload.evidence_pack,
        )
        if decision is None:
            return None
        prompt = build_extract_key_points_prompt(payload.instructions, payload.documents, payload.evidence_pack)
        response = _complete_json(prompt, context, self.name, decision=decision, purpose='task_analysis')
        if response is None:
            return None
        try:
            findings = [
                FindingItem(
                    finding_id=f'finding-{index}',
                    title=str(item.get('title') or f'关键发现 {index}'),
                    summary=str(item.get('summary') or '').strip() or '暂无摘要。',
                    citation_ids=[str(value) for value in item.get('citation_ids', []) if str(value).strip()],
                    tags=[str(value) for value in item.get('tags', []) if str(value).strip()][:4],
                )
                for index, item in enumerate(response.get('key_findings', []), start=1)
            ]
            return ExtractKeyPointsOutput(
                summary=str(response.get('summary') or '').strip(),
                key_findings=findings,
                open_questions=[str(item).strip() for item in response.get('open_questions', []) if str(item).strip()],
                confidence=float(response.get('confidence', 0.0)),
            )
        except Exception:
            return None

    def _make_title(self, text: str, index: int) -> str:
        """基于证据文本生成简短标题。"""

        compact = self._clip(re.sub(r'\s+', ' ', text).strip(), 28)
        return compact or f'关键发现 {index}'

    def _clip(self, text: str, limit: int) -> str:
        """按字符上限裁剪文本并清理空白。"""

        cleaned = re.sub(r'\s+', ' ', text).strip()
        if len(cleaned) <= limit:
            return cleaned
        return f'{cleaned[: limit - 1]}...'

    def _compute_confidence(self, coverage_score: float, evidence_count: int, open_questions: int) -> float:
        """根据覆盖度、证据量和待确认项估算置信度。"""

        base = min(1.0, coverage_score * 0.7 + min(evidence_count, 5) / 5 * 0.3)
        penalty = min(0.3, open_questions * 0.05)
        return round(max(0.1, base - penalty), 2)


class ExtractRisksTool:
    """基于启发式规则提取风险点。"""

    name = 'extract_risks'
    version = 'v1'
    timeout_ms = 20000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ExtractRisksInput
    output_model = ExtractRisksOutput

    KEYWORDS = {
        'high': ('风险', '违约', '漏洞', '阻塞', '失败', '中断', '泄露'),
        'medium': ('限制', '依赖', '待定', '缺失', '回退', '告警', '异常'),
        'low': ('建议', '注意', '优化', '评估'),
    }

    def run(self, payload: ExtractRisksInput, context) -> ExtractRisksOutput:
        """优先走 LLM 风险抽取；失败时再回退到关键词规则。"""
        llm_output = self._run_llm(payload, context)
        if llm_output is not None:
            return llm_output
        risks: list[RiskItem] = []
        seen_titles: set[str] = set()
        for index, item in enumerate(payload.evidence_pack.evidence_items, start=1):
            severity = self._detect_severity(item.text)
            if severity is None:
                continue
            title = self._make_title(item.text)
            if title in seen_titles:
                continue
            seen_titles.add(title)
            risks.append(
                RiskItem(
                    risk_id=f'risk-{index}',
                    title=title,
                    description=self._clip(item.text, 160),
                    severity=severity,
                    citation_ids=[item.citation_id],
                    recommendation='建议结合原文条款或章节上下文进一步核实。',
                )
            )
            if len(risks) >= 5:
                break
        if not risks and payload.evidence_pack.missing_aspects:
            risks.append(
                RiskItem(
                    risk_id='risk-gap-1',
                    title='证据覆盖不足',
                    description=f'当前对 {"、".join(payload.evidence_pack.missing_aspects[:4])} 的证据覆盖不足，可能造成结论遗漏。',
                    severity='medium',
                    citation_ids=[],
                    recommendation='建议补充相关章节或扩大检索范围后再次审查。',
                )
            )
        return ExtractRisksOutput(risks=risks)

    def _run_llm(self, payload: ExtractRisksInput, context) -> ExtractRisksOutput | None:
        """尝试让 LLM 直接产出结构化风险项。"""
        decision = _route_llm(
            context,
            purpose='task_analysis',
            tool_name=self.name,
            feature_enabled=context.settings.enable_task_llm_analysis,
            evidence_pack=payload.evidence_pack,
        )
        if decision is None:
            return None
        prompt = build_extract_risks_prompt(payload.instructions, payload.evidence_pack)
        response = _complete_json(prompt, context, self.name, decision=decision, purpose='task_analysis')
        if response is None:
            return None
        try:
            risks = [
                RiskItem(
                    risk_id=f'risk-{index}',
                    title=str(item.get('title') or f'风险 {index}'),
                    description=str(item.get('description') or '').strip() or '暂无描述。',
                    severity=_normalize_severity(item.get('severity')),
                    citation_ids=[str(value) for value in item.get('citation_ids', []) if str(value).strip()],
                    recommendation=str(item.get('recommendation') or '').strip() or None,
                )
                for index, item in enumerate(response.get('risks', []), start=1)
            ]
            return ExtractRisksOutput(risks=risks[:5])
        except Exception:
            return None

    def _detect_severity(self, text: str) -> SeverityLevel | None:
        """根据关键词粗分风险等级。"""

        lowered = text.lower()
        for severity, keywords in self.KEYWORDS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                return cast(SeverityLevel, severity)
        return None

    def _make_title(self, text: str) -> str:
        """基于风险文本生成简短标题。"""

        return self._clip(re.sub(r'\s+', ' ', text).strip(), 24) or '潜在风险'

    def _clip(self, text: str, limit: int) -> str:
        """按字符上限裁剪风险描述。"""

        if len(text) <= limit:
            return text
        return f'{text[: limit - 1]}...'


def _complete_json(prompt: str, context, tool_name: str, *, decision=None, purpose: str | None = None) -> dict | None:
    """让 LLM 返回 JSON；第一次解析失败时再做一次修复。"""
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
    """从一段文本里尽量提取出一个 JSON object。"""
    match = re.search(r'\{[\s\S]*\}', text)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_severity(value: object) -> SeverityLevel:
    """把风险等级规整到 low/medium/high。"""
    severity = str(value or 'medium').strip().lower()
    if severity in {'low', 'medium', 'high'}:
        return cast(SeverityLevel, severity)
    return 'medium'


def _route_llm(context, *, purpose: str, tool_name: str, feature_enabled: bool, evidence_pack: EvidencePack | None = None):
    """根据路由策略决定当前工具是否走 LLM 分支。"""

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
        evidence_count=len(evidence_pack.evidence_items) if evidence_pack is not None else 0,
        missing_aspects=len(evidence_pack.missing_aspects) if evidence_pack is not None else 0,
    )
    router.record_selection(
        context.trace,
        decision,
        scope='task_tool',
        task_id=context.task_id,
        tool_name=tool_name,
        step_name=context.step_name,
        purpose=purpose,
    )
    if decision.mode != 'llm':
        return None
    return decision

"""文档分析 Agent 提示词模块。

集中放分析、风险抽取、报告审查和 JSON 修复这些提示词模板，避免工具实现里
夹杂过多大段 prompt，并保证多个工具共享统一提示词约束。
"""

from __future__ import annotations

from app.rag_system.knowledge import DocumentContextItem
from app.models.artifact import EvidencePack, ReportArtifactContent


def build_extract_key_points_prompt(
    instructions: str,
    documents: list[DocumentContextItem],
    evidence_pack: EvidencePack,
) -> str:
    """构造关键发现提取提示词。"""
    documents_text = '\n'.join(
        f'- {item.title} | summary: {item.summary} | sections: {", ".join(item.sections[:6])}'
        for item in documents[:4]
    )
    evidence_text = '\n'.join(
        f'- {item.citation_id}: {item.text[:280]}'
        for item in evidence_pack.evidence_items[:8]
    )
    return (
        '你是 Document Analysis Agent 的分析模块。请只输出严格 JSON，不要输出额外解释、Markdown 或代码块。\n'
        '输出 schema: '
        '{"summary": str, "key_findings": [{"title": str, "summary": str, "citation_ids": [str], "tags": [str]}], '
        '"open_questions": [str], "confidence": float}\n'
        '规则:\n'
        '1. 只能输出有证据支撑的结论。\n'
        '2. citation_ids 必须来自给定证据编号。\n'
        '3. summary 需覆盖任务目标、主要发现和证据缺口。\n'
        '4. confidence 必须在 0 到 1 之间。\n'
        f'用户任务: {instructions}\n'
        f'文档上下文:\n{documents_text or "- 无"}\n'
        f'证据列表:\n{evidence_text or "- 无"}\n'
        f'证据缺口: {", ".join(evidence_pack.missing_aspects) or "无"}'
    )


def build_extract_risks_prompt(instructions: str, evidence_pack: EvidencePack) -> str:
    """构造风险抽取提示词。"""
    evidence_text = '\n'.join(
        f'- {item.citation_id}: {item.text[:320]}'
        for item in evidence_pack.evidence_items[:10]
    )
    return (
        '你是 Document Analysis Agent 的风险抽取模块。请只输出严格 JSON，不要输出额外解释。\n'
        '输出 schema: '
        '{"risks": [{"title": str, "description": str, "severity": "low|medium|high", '
        '"citation_ids": [str], "recommendation": str}]}\n'
        '规则:\n'
        '1. 只保留高价值、可行动的风险。\n'
        '2. citation_ids 必须来自给定证据编号。\n'
        '3. severity 只能是 low/medium/high。\n'
        f'审查目标: {instructions}\n'
        f'证据:\n{evidence_text or "- 无"}\n'
        f'证据缺口: {", ".join(evidence_pack.missing_aspects) or "无"}'
    )


def build_review_report_prompt(content: ReportArtifactContent) -> str:
    """构造报告审查提示词。"""
    evidence_ids = [item.citation_id for item in content.evidence]
    return (
        '你是 Document Analysis Agent 的质量审查模块。请只输出严格 JSON，不要输出额外解释。\n'
        '输出 schema: {"passed": bool, "unsupported_claims": [str], "missing_sections": [str], "review_notes": [str]}\n'
        '规则:\n'
        '1. unsupported_claims 必须填写 finding_id 或 risk_id。\n'
        '2. missing_sections 只能填写缺失字段名，例如 summary/key_findings/report_markdown/report_json。\n'
        '3. review_notes 只写高价值审查意见。\n'
        f'报告摘要: {content.summary}\n'
        f'关键发现: {[item.model_dump(mode="json") for item in content.key_findings]}\n'
        f'风险项: {[item.model_dump(mode="json") for item in content.risks]}\n'
        f'证据编号: {evidence_ids}\n'
        f'开放问题: {content.open_questions}'
    )


def build_json_repair_prompt(raw_text: str, schema_hint: str) -> str:
    """构造 JSON 修复提示词。"""
    return (
        '请把下面内容修复为合法 JSON，只输出 JSON 本身，不要输出解释。\n'
        f'目标 schema: {schema_hint}\n'
        f'原始内容:\n{raw_text}'
    )

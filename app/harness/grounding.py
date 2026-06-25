"""Grounding Runtime 实现。

负责在证据检索、分析、草稿生成和审查的主链路中建立显式的 grounding 约束，
确保所有结论都能追溯到具体证据，降低 unsupported claims 风险。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.artifact import EvidenceItem, FindingItem, ReportArtifactContent, RiskItem


class GroundingClaim(BaseModel):
    """描述一个被证据支撑的断言/结论。"""

    claim_id: str
    claim_type: str  # 'finding', 'risk', 'conclusion', 'summary'
    content: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    citation_ids: list[str] = Field(default_factory=list)
    evidence_support_score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_supported: bool = True


class GroundingBundle(BaseModel):
    """Grounding Runtime 的核心输出对象。

    包含结构化的发现、证据映射和缺失证据主题，为下游消费提供稳定的输入。
    """

    findings: list[dict] = Field(default_factory=list)
    evidence_map: dict[str, list[str]] = Field(default_factory=dict)
    missing_evidence_topics: list[str] = Field(default_factory=list)
    claims: list[GroundingClaim] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)


class GroundingResult(BaseModel):
    """Grounding 处理的完整结果。"""

    grounding_bundle: GroundingBundle
    alignment_score: float = Field(default=0.0, ge=0.0, le=1.0)
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    unsupported_claim_count: int = 0


class GroundingEngine:
    """负责证据与结论对齐的核心引擎。

    主要职责：
    1. Evidence Pack Builder - 将检索结果整理成适合下游消费的结构
    2. Claim Alignment - 将 finding/risk/conclusion 与 evidence 做显式绑定
    3. Citation Mapping - 输出从结论到证据位置的映射关系
    4. Open Question Fallback - 当证据不足时输出 OPEN_QUESTION/coverage_gap
    """

    def __init__(self) -> None:
        """初始化证据缓存。"""

        self.evidence_cache: dict[str, EvidenceItem] = {}

    def build_grounding_bundle(
        self,
        evidence_pack: Any,
        analysis: dict[str, Any] | None = None,
        draft_content: ReportArtifactContent | None = None,
    ) -> GroundingResult:
        """构建完整的 Grounding Bundle。"""
        self._cache_evidence(evidence_pack)
        
        claims = []
        evidence_map = {}
        unsupported_claims = []
        coverage_gaps = []
        
        if analysis:
            claims.extend(self._align_findings(analysis.get('key_findings', []), evidence_pack))
            claims.extend(self._align_risks(analysis.get('risks', []), evidence_pack))
        
        if draft_content:
            claims.extend(self._align_summary(draft_content, evidence_pack))
            claims.extend(self._align_report_claims(draft_content, evidence_pack))
        
        evidence_map = self._build_citation_map(claims, evidence_pack)
        unsupported_claims = [claim.content for claim in claims if not claim.is_supported]
        coverage_gaps = self._detect_coverage_gaps(evidence_pack, claims)
        
        alignment_score = self._calculate_alignment_score(claims)
        coverage_ratio = self._calculate_coverage_ratio(evidence_pack, claims)
        
        grounding_bundle = GroundingBundle(
            findings=analysis.get('key_findings', []) if analysis else [],
            evidence_map=evidence_map,
            missing_evidence_topics=list(evidence_pack.missing_aspects) if evidence_pack else [],
            claims=claims,
            unsupported_claims=unsupported_claims,
            coverage_gaps=coverage_gaps,
        )
        
        return GroundingResult(
            grounding_bundle=grounding_bundle,
            alignment_score=alignment_score,
            coverage_ratio=coverage_ratio,
            unsupported_claim_count=len(unsupported_claims),
        )

    def _cache_evidence(self, evidence_pack: Any) -> None:
        """缓存证据项以便快速查找。"""
        if evidence_pack and hasattr(evidence_pack, 'evidence_items'):
            self.evidence_cache = {
                item.citation_id: item
                for item in evidence_pack.evidence_items
            }

    def _align_findings(self, findings: list[dict], evidence_pack: Any) -> list[GroundingClaim]:
        """将关键发现与证据对齐。"""
        claims: list[GroundingClaim] = []
        
        for finding in findings:
            finding_item = FindingItem(**finding) if isinstance(finding, dict) else finding
            claim = self._create_claim_from_finding(finding_item, evidence_pack)
            claims.append(claim)
        
        return claims

    def _align_risks(self, risks: list[dict], evidence_pack: Any) -> list[GroundingClaim]:
        """将风险项与证据对齐。"""
        claims: list[GroundingClaim] = []
        
        for risk in risks:
            risk_item = RiskItem(**risk) if isinstance(risk, dict) else risk
            claim = self._create_claim_from_risk(risk_item, evidence_pack)
            claims.append(claim)
        
        return claims

    def _align_summary(self, draft_content: ReportArtifactContent, evidence_pack: Any) -> list[GroundingClaim]:
        """将摘要与证据对齐。"""
        if not draft_content.summary.strip():
            return []
        
        claim = GroundingClaim(
            claim_id='summary-1',
            claim_type='summary',
            content=draft_content.summary,
            confidence=draft_content.confidence,
            citation_ids=[],
            evidence_support_score=self._calculate_support_score(draft_content.summary, evidence_pack),
            is_supported=True,
        )
        
        return [claim]

    def _align_report_claims(self, draft_content: ReportArtifactContent, evidence_pack: Any) -> list[GroundingClaim]:
        """对齐报告中的所有断言。"""
        claims: list[GroundingClaim] = []
        
        for idx, finding in enumerate(draft_content.key_findings, start=1):
            claim = self._create_claim_from_finding(finding, evidence_pack)
            claims.append(claim)
        
        for idx, risk in enumerate(draft_content.risks, start=1):
            claim = self._create_claim_from_risk(risk, evidence_pack)
            claims.append(claim)
        
        return claims

    def _create_claim_from_finding(self, finding: FindingItem, evidence_pack: Any) -> GroundingClaim:
        """从 FindingItem 创建 GroundingClaim。"""
        supported, support_score = self._verify_citation_support(finding.citation_ids, evidence_pack)
        
        return GroundingClaim(
            claim_id=finding.finding_id,
            claim_type='finding',
            content=finding.title,
            confidence=self._estimate_confidence(finding, evidence_pack),
            citation_ids=finding.citation_ids,
            evidence_support_score=support_score,
            is_supported=supported,
        )

    def _create_claim_from_risk(self, risk: RiskItem, evidence_pack: Any) -> GroundingClaim:
        """从 RiskItem 创建 GroundingClaim。"""
        supported, support_score = self._verify_citation_support(risk.citation_ids, evidence_pack)
        
        return GroundingClaim(
            claim_id=risk.risk_id,
            claim_type='risk',
            content=risk.title,
            confidence=self._estimate_risk_confidence(risk, evidence_pack),
            citation_ids=risk.citation_ids,
            evidence_support_score=support_score,
            is_supported=supported,
        )

    def _verify_citation_support(self, citation_ids: list[str], evidence_pack: Any) -> tuple[bool, float]:
        """验证引用是否有证据支撑。"""
        if not citation_ids:
            return False, 0.0
        
        valid_count = 0
        total_score = 0.0
        
        if evidence_pack and hasattr(evidence_pack, 'evidence_items'):
            evidence_by_id = {item.citation_id: item for item in evidence_pack.evidence_items}
            
            for citation_id in citation_ids:
                if citation_id in evidence_by_id:
                    valid_count += 1
                    total_score += evidence_by_id[citation_id].support_score
        
        if valid_count == 0:
            return False, 0.0
        
        support_ratio = valid_count / len(citation_ids)
        avg_score = total_score / valid_count if valid_count > 0 else 0.0
        
        return support_ratio >= 0.5, min(1.0, support_ratio * avg_score)

    def _calculate_support_score(self, content: str, evidence_pack: Any) -> float:
        """计算内容的证据支撑分数。"""
        if not evidence_pack or not hasattr(evidence_pack, 'evidence_items'):
            return 0.0
        
        evidence_text = '\n'.join(item.text for item in evidence_pack.evidence_items).lower()
        content_lower = content.lower()
        
        keywords = [word for word in content_lower.split() if len(word) >= 4]
        if not keywords:
            return 0.0
        
        matched = sum(1 for keyword in keywords if keyword in evidence_text)
        return min(1.0, matched / len(keywords))

    def _estimate_confidence(self, finding: FindingItem, evidence_pack: Any) -> float:
        """估算发现的置信度。"""
        base_confidence = 0.5
        
        if finding.citation_ids:
            evidence_count = len(finding.citation_ids)
            base_confidence = min(1.0, 0.5 + evidence_count * 0.1)
        
        if evidence_pack and hasattr(evidence_pack, 'coverage_score'):
            base_confidence = (base_confidence + float(evidence_pack.coverage_score)) / 2
        
        return round(base_confidence, 2)

    def _estimate_risk_confidence(self, risk: RiskItem, evidence_pack: Any) -> float:
        """估算风险的置信度。"""
        base_confidence = 0.6
        
        if risk.citation_ids:
            evidence_count = len(risk.citation_ids)
            base_confidence = min(1.0, 0.5 + evidence_count * 0.15)
        
        if evidence_pack and hasattr(evidence_pack, 'coverage_score'):
            base_confidence = (base_confidence + float(evidence_pack.coverage_score)) / 2
        
        return round(base_confidence, 2)

    def _build_citation_map(self, claims: list[GroundingClaim], evidence_pack: Any) -> dict[str, list[str]]:
        """构建从结论到证据位置的映射。"""
        citation_map: dict[str, list[str]] = {}
        
        if not evidence_pack or not hasattr(evidence_pack, 'evidence_items'):
            return citation_map
        
        evidence_by_id = {item.citation_id: item for item in evidence_pack.evidence_items}
        
        for claim in claims:
            locations = []
            for citation_id in claim.citation_ids:
                if citation_id in evidence_by_id:
                    evidence = evidence_by_id[citation_id]
                    location = f"{evidence.source}"
                    if evidence.page:
                        location += f":{evidence.page}"
                    locations.append(location)
            citation_map[claim.claim_id] = locations
        
        return citation_map

    def _detect_coverage_gaps(self, evidence_pack: Any, claims: list[GroundingClaim]) -> list[str]:
        """检测证据覆盖缺口。"""
        gaps: list[str] = []
        
        if evidence_pack and hasattr(evidence_pack, 'missing_aspects'):
            gaps.extend(list(evidence_pack.missing_aspects))
        
        unsupported_claims = [claim for claim in claims if not claim.is_supported]
        for claim in unsupported_claims:
            gaps.append(f"UNSUPPORTED_CLAIM: {claim.content[:50]}...")
        
        return gaps[:10]

    def _calculate_alignment_score(self, claims: list[GroundingClaim]) -> float:
        """计算整体对齐分数。"""
        if not claims:
            return 0.0
        
        supported_count = sum(1 for claim in claims if claim.is_supported)
        avg_support_score = sum(claim.evidence_support_score for claim in claims) / len(claims)
        
        return round((supported_count / len(claims)) * avg_support_score, 2)

    def _calculate_coverage_ratio(self, evidence_pack: Any, claims: list[GroundingClaim]) -> float:
        """计算证据覆盖比率。"""
        if not evidence_pack or not hasattr(evidence_pack, 'evidence_items'):
            return 0.0
        
        total_evidence = len(evidence_pack.evidence_items)
        if total_evidence == 0:
            return 0.0
        
        used_citations = set()
        for claim in claims:
            used_citations.update(claim.citation_ids)
        
        evidence_by_id = {item.citation_id: item for item in evidence_pack.evidence_items}
        used_count = sum(1 for citation_id in used_citations if citation_id in evidence_by_id)
        
        return round(used_count / total_evidence, 2)

    def build_open_question_fallback(self, context: str, missing_aspects: list[str]) -> dict[str, Any]:
        """构建证据不足时的 OPEN_QUESTION 降级输出。"""
        return {
            'type': 'OPEN_QUESTION',
            'context': context,
            'missing_aspects': missing_aspects,
            'coverage_gap': True,
            'suggestion': '当前证据不足以支撑结论，请补充相关文档或扩大检索范围。',
        }

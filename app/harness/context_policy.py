"""Context Policy 定义。

为不同 workflow step 定义上下文选取规则与上限，实现精细的上下文控制。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ContextSourceType(str, Enum):
    """上下文来源类型。"""
    STATE = 'state'
    EVIDENCE = 'evidence'
    MEMORY = 'memory'
    ARTIFACT = 'artifact'


class StepType(str, Enum):
    """工作流步骤类型。"""
    COLLECT_DOCUMENT_CONTEXT = 'collect_document_context'
    RETRIEVE_EVIDENCE = 'retrieve_evidence'
    HANDLE_EVIDENCE_GAP = 'handle_evidence_gap'
    ANALYZE = 'analyze'
    DRAFT_ARTIFACT = 'draft_artifact'
    REVIEW_ARTIFACT = 'review_artifact'
    REVISE_ARTIFACT = 'revise_artifact'
    FINALIZE = 'finalize'


class ContextSelectionRule(BaseModel):
    """单个上下文源的选取规则。"""
    source_type: ContextSourceType
    enabled: bool = True
    top_k: int = Field(default=5, ge=0, le=50)
    relevance_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    max_chars: int = Field(default=3000, ge=100, le=30000)
    include_fields: list[str] = Field(default_factory=list)
    exclude_fields: list[str] = Field(default_factory=list)


class ContextPolicy(BaseModel):
    """特定 step 的上下文策略配置。"""
    step_type: StepType | str
    description: str = ''
    
    evidence_top_k: int = Field(default=6, ge=0, le=20)
    evidence_relevance_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    
    memory_limit: int = Field(default=3, ge=0, le=10)
    reflection_limit: int = Field(default=2, ge=0, le=5)
    artifact_memory_limit: int = Field(default=2, ge=0, le=5)
    
    artifact_scope: Literal['full', 'summary', 'questions', 'none'] = 'summary'
    
    document_limit: int = Field(default=6, ge=1, le=20)
    risk_limit: int = Field(default=5, ge=0, le=10)
    
    compression_enabled: bool = True
    compression_max_sentences: int = Field(default=3, ge=1, le=10)
    compression_max_chars: int = Field(default=1200, ge=100, le=5000)
    
    token_budget: int = Field(default=8000, ge=100, le=32000)
    budget_priority: list[ContextSourceType] = Field(
        default_factory=lambda: [ContextSourceType.EVIDENCE, ContextSourceType.STATE, ContextSourceType.MEMORY, ContextSourceType.ARTIFACT]
    )
    
    selection_rules: list[ContextSelectionRule] = Field(default_factory=list)
    
    @classmethod
    def for_step(cls, step_id: str) -> 'ContextPolicy':
        """根据 step_id 获取预设的上下文策略。"""
        step_mapping: dict[str, ContextPolicy] = {
            'collect_document_context': cls(
                step_type='collect_document_context',
                description='收集文档上下文',
                evidence_top_k=0,
                memory_limit=0,
                artifact_scope='none',
                document_limit=10,
                token_budget=4000,
            ),
            'retrieve_evidence': cls(
                step_type='retrieve_evidence',
                description='检索证据',
                evidence_top_k=10,
                evidence_relevance_threshold=0.2,
                memory_limit=1,
                artifact_scope='none',
                document_limit=5,
                token_budget=6000,
            ),
            'handle_evidence_gap': cls(
                step_type='handle_evidence_gap',
                description='处理证据缺口',
                evidence_top_k=12,
                evidence_relevance_threshold=0.15,
                memory_limit=2,
                artifact_scope='summary',
                document_limit=5,
                token_budget=6000,
            ),
            'analyze': cls(
                step_type='analyze',
                description='分析文档',
                evidence_top_k=8,
                evidence_relevance_threshold=0.3,
                memory_limit=2,
                reflection_limit=1,
                artifact_scope='none',
                document_limit=6,
                risk_limit=5,
                compression_max_sentences=3,
                compression_max_chars=1500,
                token_budget=10000,
            ),
            'draft_artifact': cls(
                step_type='draft_artifact',
                description='生成初稿',
                evidence_top_k=6,
                evidence_relevance_threshold=0.4,
                memory_limit=3,
                reflection_limit=2,
                artifact_scope='none',
                document_limit=6,
                risk_limit=5,
                compression_max_sentences=4,
                compression_max_chars=2000,
                token_budget=12000,
            ),
            'review_artifact': cls(
                step_type='review_artifact',
                description='审核产物',
                evidence_top_k=6,
                evidence_relevance_threshold=0.35,
                memory_limit=3,
                reflection_limit=2,
                artifact_scope='full',
                document_limit=6,
                risk_limit=5,
                compression_max_sentences=3,
                compression_max_chars=1800,
                token_budget=10000,
            ),
            'revise_artifact': cls(
                step_type='revise_artifact',
                description='修订产物',
                evidence_top_k=8,
                evidence_relevance_threshold=0.3,
                memory_limit=3,
                reflection_limit=2,
                artifact_scope='full',
                document_limit=6,
                risk_limit=5,
                compression_max_sentences=4,
                compression_max_chars=2000,
                token_budget=12000,
            ),
            'finalize': cls(
                step_type='finalize',
                description='最终定稿',
                evidence_top_k=5,
                evidence_relevance_threshold=0.4,
                memory_limit=2,
                reflection_limit=1,
                artifact_scope='summary',
                document_limit=5,
                risk_limit=3,
                compression_max_sentences=2,
                compression_max_chars=1000,
                token_budget=8000,
            ),
        }
        return step_mapping.get(step_id, cls(step_type=step_id))
    
    def get_selection_rule(self, source_type: ContextSourceType) -> ContextSelectionRule:
        """获取特定来源类型的选取规则。"""
        for rule in self.selection_rules:
            if rule.source_type == source_type:
                return rule
        return ContextSelectionRule(
            source_type=source_type,
            top_k=self._get_default_top_k(source_type),
            relevance_threshold=self.evidence_relevance_threshold if source_type == ContextSourceType.EVIDENCE else 0.0,
            max_chars=self._get_default_max_chars(source_type),
        )
    
    def _get_default_top_k(self, source_type: ContextSourceType) -> int:
        """返回指定来源类型的默认条目上限。"""

        defaults = {
            ContextSourceType.EVIDENCE: self.evidence_top_k,
            ContextSourceType.MEMORY: self.memory_limit,
            ContextSourceType.ARTIFACT: self.artifact_memory_limit,
            ContextSourceType.STATE: 10,
        }
        return defaults.get(source_type, 5)
    
    def _get_default_max_chars(self, source_type: ContextSourceType) -> int:
        """返回指定来源类型的默认字符预算。"""

        defaults = {
            ContextSourceType.EVIDENCE: self.compression_max_chars,
            ContextSourceType.MEMORY: 1000,
            ContextSourceType.ARTIFACT: 3000,
            ContextSourceType.STATE: 2000,
        }
        return defaults.get(source_type, 1500)

"""RAG 系统 Knowledge 策略契约模块。

定义 grounded answer 阶段的策略开关与质量评估结构。
与主应用的 `app/capabilities/knowledge/contracts.py` 功能一致。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalQualityReport(BaseModel):
    """知识检索/回答阶段的质量评估结果。"""
    enabled: bool = False
    supported: bool = True
    risk: str = 'low'
    confidence: float = 1.0
    reason: str = 'disabled'
    rewrite_needed: bool = False
    applied: bool = False
    check_mode: str = 'disabled'
    final_mode: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GroundedAnswerStrategy(BaseModel):
    """Knowledge Capability 的 grounded answer 策略。"""
    use_corrective_rag: bool = False
    use_graph_rag: bool = False
    use_hybrid_retrieval: bool = True
    use_rerank: bool = True
    graph_max_hops: int = Field(default=2, ge=1, le=5)

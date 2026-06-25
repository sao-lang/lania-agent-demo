"""Context builder 共享模型模块。

定义上下文构建阶段输出的结构化结果，方便 ContextHarness 与后续执行层共享
优化状态、预算信息和 grounding 结果，而不是回退到松散字典。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.harness.context_policy import ContextPolicy
from app.harness.grounding import GroundingResult
from app.harness.models import ContextBundle


class ContextOptimizationResult(BaseModel):
    """上下文优化结果。

    汇总最终可用的 ``ContextBundle``、命中的策略、预算消耗与压缩收益，便于
    调试和观测上下文裁剪过程。
    """

    context_bundle: ContextBundle
    policy: ContextPolicy
    budget_status: dict[str, Any]
    compression_ratio: float = 0.0
    saved_tokens: int = 0
    optimization_info: dict[str, Any] = {}
    grounding_result: GroundingResult | None = None

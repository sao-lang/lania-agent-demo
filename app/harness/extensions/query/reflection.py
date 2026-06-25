"""查询反思扩展模块。

负责把 query runtime 在 corrective RAG 阶段得到的状态，归并成统一的
``ReflectionDecision`` 结构，供上层决定是否继续检索、改写答案或直接接受
结果。模块本身不执行检索与生成，只承担决策结果的格式化职责。
"""

from __future__ import annotations

from app.models.query import QueryRequest
from app.models.task import ReflectionDecision


class ReflectionHarness:
    """统一构造 query runtime 的结构化 reflection 决策。"""

    def build_query_reflection_decision(
        self,
        *,
        request: QueryRequest,
        corrective_info: dict,
        retry_count: int,
        max_retry_count: int,
        retry_enabled: bool,
        min_grounding_confidence: float,
    ) -> ReflectionDecision:
        """根据 corrective 信息生成查询阶段的反思决策。

        Args:
            request: 当前查询请求，包含是否启用 corrective RAG 等开关。
            corrective_info: grounding 校正阶段返回的结构化信息。
            retry_count: 当前已经执行的重试次数。
            max_retry_count: 允许的最大重试次数。
            retry_enabled: 当前运行时是否允许继续重试。
            min_grounding_confidence: 触发再次检索所需的最小 grounding 置信度。

        Returns:
            标准化后的反思决策对象。
        """

        retry_allowed = retry_enabled and retry_count < max_retry_count
        applied = bool(corrective_info.get('applied'))
        should_retry = bool(
            request.use_corrective_rag
            and corrective_info.get('enabled')
            and applied
            and retry_allowed
            and float(corrective_info.get('confidence') or 0.0) < min_grounding_confidence
        )
        if should_retry:
            return ReflectionDecision(
                decision='retry_retrieve',
                reason=str(corrective_info.get('reason') or 'low_grounding_confidence'),
                should_continue=True,
                fallback_action='retry',
                exit_reason='retry_retrieve',
                confidence=float(corrective_info.get('confidence') or 0.0),
                risk=str(corrective_info.get('risk') or '') or None,
                supported=corrective_info.get('supported'),
                final_mode=str(corrective_info.get('final_mode') or '') or None,
            )
        if applied:
            return ReflectionDecision(
                decision='rewrite_answer',
                reason=str(corrective_info.get('reason') or 'corrective_rewrite_applied'),
                should_continue=False,
                fallback_action='degrade',
                exit_reason='corrective_rewrite_applied',
                confidence=float(corrective_info.get('confidence') or 0.0),
                risk=str(corrective_info.get('risk') or '') or None,
                supported=corrective_info.get('supported'),
                final_mode=str(corrective_info.get('final_mode') or '') or None,
            )
        return ReflectionDecision(
            decision='accept',
            reason=str(corrective_info.get('reason') or 'grounded_by_context'),
            should_continue=False,
            fallback_action=None,
            exit_reason='accepted',
            confidence=float(corrective_info.get('confidence') or 0.0) if corrective_info.get('confidence') is not None else None,
            risk=str(corrective_info.get('risk') or '') or None,
            supported=corrective_info.get('supported'),
            final_mode=str(corrective_info.get('final_mode') or '') or None,
        )

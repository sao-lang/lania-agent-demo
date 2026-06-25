"""最小可用的模型路由与成本调度。

当前实现先解决两件事：
1. 让 task/query/knowledge 在进入 LLM 前先形成统一路由决策；
2. 在只有单个 LLM provider 的前提下，先支持「是否使用 LLM」与「成本档位」两类调度。
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

ModelRouteMode = Literal['llm', 'fallback']
ModelRouteProfile = Literal['economy', 'balanced', 'quality', 'disabled']
ModelRoutePurpose = Literal[
    'task_analysis',
    'task_review',
    'knowledge_answer',
    'knowledge_check',
    'knowledge_rewrite',
    'json_repair',
]


class ModelRouteDecision(BaseModel):
    """描述一次模型路由结果。"""

    purpose: ModelRoutePurpose
    mode: ModelRouteMode = 'fallback'
    profile: ModelRouteProfile = 'disabled'
    reason: str = 'llm_unavailable'
    estimated_cost_units: float = 0.0
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ModelUsageSnapshot(BaseModel):
    """描述一次实际模型消费快照。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    actual_cost_units: float = 0.0
    cost_source: Literal['provider_cost', 'provider_usage', 'local_estimate'] = 'local_estimate'
    provider_reported: bool = False


class ModelRouter:
    """根据运行预算与用途决定是否使用 LLM。"""

    PURPOSE_COST = {
        'task_analysis': {'economy': 0.8, 'balanced': 1.6, 'quality': 2.6},
        'task_review': {'economy': 0.7, 'balanced': 1.4, 'quality': 2.3},
        'knowledge_answer': {'economy': 0.6, 'balanced': 1.2, 'quality': 2.0},
        'knowledge_check': {'economy': 0.4, 'balanced': 0.8, 'quality': 1.4},
        'knowledge_rewrite': {'economy': 0.8, 'balanced': 1.5, 'quality': 2.4},
        'json_repair': {'economy': 0.2, 'balanced': 0.3, 'quality': 0.5},
    }

    def route(
        self,
        *,
        purpose: ModelRoutePurpose,
        llm_available: bool,
        feature_enabled: bool = True,
        run_budget: Any | None = None,
        step_name: str | None = None,
        evidence_count: int = 0,
        missing_aspects: int = 0,
    ) -> ModelRouteDecision:
        """为一次模型调用选择模式与成本档位。"""

        if not llm_available:
            return ModelRouteDecision(purpose=purpose, reason='llm_unavailable')
        if not feature_enabled:
            return ModelRouteDecision(purpose=purpose, reason='feature_disabled')

        profile = self._select_profile(
            purpose=purpose,
            run_budget=run_budget,
            step_name=step_name,
            evidence_count=evidence_count,
            missing_aspects=missing_aspects,
        )
        estimated_cost = self.PURPOSE_COST[purpose][profile]
        return ModelRouteDecision(
            purpose=purpose,
            mode='llm',
            profile=profile,
            reason='route_selected',
            estimated_cost_units=estimated_cost,
            metadata={
                'step_name': step_name,
                'evidence_count': evidence_count,
                'missing_aspects': missing_aspects,
                'budget_max_steps': getattr(run_budget, 'max_steps', None),
                'budget_max_tool_calls': getattr(run_budget, 'max_tool_calls', None),
            },
        )

    def record_selection(self, trace, decision: ModelRouteDecision, *, scope: str, **metadata: Any) -> None:
        """记录一次模型路由选择结果。"""
        trace.record(
            'model_route_selected',
            {
                'scope': scope,
                **metadata,
                **decision.model_dump(mode='json'),
            },
        )

    def record_completion(
        self,
        trace,
        decision: ModelRouteDecision,
        *,
        prompt_text: str,
        response: Any,
        response_text: str | None = None,
        scope: str,
        **metadata: Any,
    ) -> ModelUsageSnapshot:
        """记录一次实际模型消费回写。"""
        usage = self.capture_usage(
            decision,
            prompt_text=prompt_text,
            response=response,
            response_text=response_text,
        )
        trace.record(
            'model_route_consumed',
            {
                'scope': scope,
                **metadata,
                **decision.model_dump(mode='json'),
                **usage.model_dump(mode='json'),
            },
        )
        return usage

    def capture_usage(
        self,
        decision: ModelRouteDecision,
        *,
        prompt_text: str,
        response: Any,
        response_text: str | None = None,
    ) -> ModelUsageSnapshot:
        """从 provider usage 或本地估算中提取成本快照。"""
        usage_payload = self._extract_usage_payload(response)
        prompt_tokens = self._first_int(usage_payload, 'prompt_tokens', 'input_tokens', 'prompt_token_count')
        completion_tokens = self._first_int(usage_payload, 'completion_tokens', 'output_tokens', 'completion_token_count')
        total_tokens = self._first_int(usage_payload, 'total_tokens', 'token_count')
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens
        if prompt_tokens <= 0 or completion_tokens <= 0 or total_tokens <= 0:
            estimated_prompt_tokens = self._estimate_token_count(prompt_text)
            estimated_completion_tokens = self._estimate_token_count(
                response_text if response_text is not None else self._extract_response_text(response)
            )
            if prompt_tokens <= 0:
                prompt_tokens = estimated_prompt_tokens
            if completion_tokens <= 0:
                completion_tokens = estimated_completion_tokens
            if total_tokens <= 0:
                total_tokens = prompt_tokens + completion_tokens
        provider_cost = self._first_float(usage_payload, 'cost', 'total_cost', 'cost_units')
        if provider_cost > 0.0:
            return ModelUsageSnapshot(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                actual_cost_units=provider_cost,
                cost_source='provider_cost',
                provider_reported=True,
            )
        provider_reported = bool(usage_payload)
        cost_source: Literal['provider_cost', 'provider_usage', 'local_estimate'] = (
            'provider_usage' if provider_reported else 'local_estimate'
        )
        actual_cost_units = round((max(total_tokens, 0) / 1000.0) * max(decision.estimated_cost_units, 0.1), 4)
        return ModelUsageSnapshot(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            actual_cost_units=actual_cost_units,
            cost_source=cost_source,
            provider_reported=provider_reported,
        )

    def _select_profile(
        self,
        *,
        purpose: ModelRoutePurpose,
        run_budget: Any | None,
        step_name: str | None,
        evidence_count: int,
        missing_aspects: int,
    ) -> ModelRouteProfile:
        """根据用途、预算和上下文复杂度选择成本档位。"""

        if purpose in {'task_review', 'knowledge_rewrite'}:
            return 'quality'
        if purpose == 'json_repair':
            return 'economy'
        if purpose == 'knowledge_check':
            return 'balanced'
        if run_budget is None:
            return 'balanced'
        if run_budget.max_steps <= 4 or run_budget.max_tool_calls <= 8:
            return 'economy'
        if purpose == 'task_analysis' and (evidence_count >= 6 or missing_aspects > 0):
            return 'quality'
        if purpose == 'knowledge_answer' and step_name == 'grounded_answer':
            return 'quality'
        return 'balanced'

    def _extract_usage_payload(self, response: Any) -> dict[str, Any]:
        """从不同 provider 响应对象中提取 usage 信息。"""

        candidates: list[Any] = []
        if response is not None:
            candidates.extend(
                [
                    getattr(response, 'usage', None),
                    getattr(response, 'additional_kwargs', None),
                    getattr(getattr(response, 'raw', None), 'usage', None),
                    getattr(response, 'raw', None),
                    response,
                ]
            )
        for candidate in candidates:
            payload = self._coerce_usage_payload(candidate)
            if payload:
                return payload
        return {}

    def _coerce_usage_payload(self, candidate: Any) -> dict[str, Any]:
        """把 provider 返回对象规整为统一 usage 字典。"""

        if candidate is None:
            return {}
        if isinstance(candidate, dict):
            if isinstance(candidate.get('usage'), dict):
                return dict(candidate['usage'])
            return dict(candidate)
        for attr in ('model_dump', 'dict'):
            if hasattr(candidate, attr):
                try:
                    value = getattr(candidate, attr)()
                except TypeError:
                    continue
                if isinstance(value, dict):
                    if isinstance(value.get('usage'), dict):
                        return dict(value['usage'])
                    return dict(value)
        payload: dict[str, Any] = {}
        for key in ('prompt_tokens', 'input_tokens', 'completion_tokens', 'output_tokens', 'total_tokens', 'cost', 'total_cost'):
            value = getattr(candidate, key, None)
            if value is not None:
                payload[key] = value
        return payload

    def _estimate_token_count(self, text: str | None) -> int:
        """基于字符数粗估 token 数量。"""

        normalized = str(text or '').strip()
        if not normalized:
            return 0
        return max(1, int(math.ceil(len(normalized) / 4)))

    def _extract_response_text(self, response: Any) -> str:
        """提取响应对象中的文本内容，供本地估算 token。"""

        if response is None:
            return ''
        text = getattr(response, 'text', None)
        if text is not None:
            return str(text)
        return str(response)

    def _first_int(self, payload: dict[str, Any], *keys: str) -> int:
        """按候选字段顺序读取第一个可用整数值。"""

        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    def _first_float(self, payload: dict[str, Any], *keys: str) -> float:
        """按候选字段顺序读取第一个可用浮点值。"""

        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        return 0.0

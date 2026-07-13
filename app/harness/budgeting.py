"""Token Budget Engine 实现。

智能预算分配与超预算时的优先级裁剪策略，确保上下文在 token 限制内。
"""

from __future__ import annotations

from typing import Any

from app.harness.context_policy import ContextPolicy, ContextSourceType


class BudgetAllocation:
    """预算分配结果。"""

    def __init__(self, source_type: ContextSourceType, allocated: int, used: int = 0):
        """记录单个上下文来源的预算与已使用量。"""

        self.source_type = source_type
        self.allocated = allocated
        self.used = used

    @property
    def remaining(self) -> int:
        """剩余预算。"""
        return max(0, self.allocated - self.used)

    @property
    def over_budget(self) -> bool:
        """是否超预算。"""
        return self.used > self.allocated


class TokenBudgetEngine:
    """Token 预算引擎。"""

    CHARS_TO_TOKENS_RATIO = 4

    def __init__(self):
        """初始化预算分配表。"""

        self.allocations: dict[ContextSourceType, BudgetAllocation] = {}

    def allocate_budget(self, policy: ContextPolicy) -> None:
        """根据策略分配预算。"""
        total_budget = policy.token_budget

        weights = self._calculate_weights(policy)
        total_weight = sum(weights.values())

        if total_weight == 0:
            total_weight = len(weights)

        allocations: dict[ContextSourceType, int] = {}
        allocated_total = 0

        for source_type, weight in weights.items():
            allocated = int(total_budget * (weight / total_weight))
            allocations[source_type] = allocated
            allocated_total += allocated

        remaining = total_budget - allocated_total
        if remaining != 0:
            for i, source_type in enumerate(weights.keys()):
                if remaining == 0:
                    break
                allocations[source_type] += 1
                remaining -= 1

        for source_type, allocated in allocations.items():
            self.allocations[source_type] = BudgetAllocation(source_type, allocated)

    def _calculate_weights(self, policy: ContextPolicy) -> dict[ContextSourceType, float]:
        """计算各来源类型的权重。"""
        base_weights = {
            ContextSourceType.EVIDENCE: 0.4,
            ContextSourceType.STATE: 0.25,
            ContextSourceType.MEMORY: 0.15,
            ContextSourceType.ARTIFACT: 0.2,
        }

        priority_order = policy.budget_priority

        priority_boost = {}
        for i, source_type in enumerate(priority_order):
            priority_boost[source_type] = 1.0 + (len(priority_order) - i) * 0.1

        weights = {}
        for source_type, base_weight in base_weights.items():
            boost = priority_boost.get(source_type, 1.0)
            weights[source_type] = base_weight * boost

        return weights

    def estimate_tokens(self, data: Any) -> int:
        """估算数据的 token 数量。"""
        if data is None:
            return 0

        if isinstance(data, str):
            return len(data) // self.CHARS_TO_TOKENS_RATIO

        if isinstance(data, list):
            return sum(self.estimate_tokens(item) for item in data)

        if isinstance(data, dict):
            total = 0
            for key, value in data.items():
                total += self.estimate_tokens(key)
                total += self.estimate_tokens(value)
            return total

        return len(str(data)) // self.CHARS_TO_TOKENS_RATIO

    def record_usage(self, source_type: ContextSourceType, data: Any) -> None:
        """记录预算使用情况。"""
        if source_type not in self.allocations:
            return

        tokens = self.estimate_tokens(data)
        self.allocations[source_type].used += tokens

    def check_budget(self, source_type: ContextSourceType) -> bool:
        """检查特定来源是否超预算。"""
        allocation = self.allocations.get(source_type)
        return allocation is not None and not allocation.over_budget

    def get_total_usage(self) -> int:
        """获取总 token 使用量。"""
        return sum(alloc.used for alloc in self.allocations.values())

    def get_total_budget(self) -> int:
        """获取总预算。"""
        return sum(alloc.allocated for alloc in self.allocations.values())

    def get_budget_status(self) -> dict[str, Any]:
        """获取预算状态摘要。"""
        status = {
            'total_budget': self.get_total_budget(),
            'total_used': self.get_total_usage(),
            'remaining': max(0, self.get_total_budget() - self.get_total_usage()),
            'by_source': {},
        }

        for source_type, alloc in self.allocations.items():
            status['by_source'][source_type.value] = {
                'allocated': alloc.allocated,
                'used': alloc.used,
                'remaining': alloc.remaining,
                'over_budget': alloc.over_budget,
            }

        return status

    def enforce_budget(self, context_data: dict[str, Any], policy: ContextPolicy) -> dict[str, Any]:
        """强制执行预算约束，裁剪超预算部分。"""
        result = dict(context_data)

        if self.get_total_usage() <= self.get_total_budget():
            return result

        priority_order = policy.budget_priority

        for source_type in reversed(priority_order):
            if not self.allocations[source_type].over_budget:
                continue

            result = self._trim_source(result, source_type, policy)

        return result

    def _trim_source(self, context_data: dict[str, Any], source_type: ContextSourceType, policy: ContextPolicy) -> dict[str, Any]:
        """裁剪特定来源的数据以满足预算。"""
        result = dict(context_data)
        allocation = self.allocations[source_type]

        if not allocation.over_budget:
            return result

        over_used = allocation.used - allocation.allocated

        mapping = {
            ContextSourceType.EVIDENCE: 'evidence_slice',
            ContextSourceType.STATE: 'state_slice',
            ContextSourceType.MEMORY: 'memory_slice',
            ContextSourceType.ARTIFACT: 'artifact_slice',
        }

        key = mapping.get(source_type)
        if key not in result:
            return result

        data = result[key]
        if data is None:
            return result

        chars_to_remove = over_used * self.CHARS_TO_TOKENS_RATIO

        result[key] = self._trim_data(data, chars_to_remove, source_type, policy)

        new_usage = self.estimate_tokens(result[key])
        allocation.used = new_usage

        return result

    def _trim_data(self, data: Any, chars_to_remove: int, source_type: ContextSourceType, policy: ContextPolicy) -> Any:
        """按字符数裁剪数据。"""
        if data is None:
            return None

        if isinstance(data, str):
            if len(data) <= chars_to_remove:
                return ''
            return data[:-chars_to_remove]

        if isinstance(data, list):
            total_chars = sum(len(str(item)) for item in data)
            if total_chars <= chars_to_remove:
                return []

            while chars_to_remove > 0 and data:
                last_item = data[-1]
                item_chars = len(str(last_item))
                if item_chars <= chars_to_remove:
                    data.pop()
                    chars_to_remove -= item_chars
                else:
                    data[-1] = self._trim_data(last_item, chars_to_remove, source_type, policy)
                    break

            return data

        if isinstance(data, dict):
            text_fields = ['text', 'summary', 'content', 'report_markdown']
            for field in text_fields:
                if field in data and isinstance(data[field], str):
                    field_chars = len(data[field])
                    if field_chars > 0:
                        trim_amount = min(chars_to_remove, field_chars // 2)
                        data[field] = data[field][:-trim_amount]
                        chars_to_remove -= trim_amount
                        if chars_to_remove <= 0:
                            break

            list_fields = ['risks', 'open_questions', 'task_memory', 'reflections']
            for field in list_fields:
                if field in data and isinstance(data[field], list):
                    while chars_to_remove > 0 and data[field]:
                        data[field].pop()
                        chars_to_remove -= 100

            return data

        return data

    def calculate_savings(self, original: dict[str, Any], optimized: dict[str, Any]) -> dict[str, Any]:
        """计算优化前后的 token 节省。"""
        original_tokens = self.estimate_tokens(original)
        optimized_tokens = self.estimate_tokens(optimized)

        return {
            'original_tokens': original_tokens,
            'optimized_tokens': optimized_tokens,
            'saved_tokens': max(0, original_tokens - optimized_tokens),
            'savings_rate': 0.0 if original_tokens == 0 else (original_tokens - optimized_tokens) / original_tokens,
        }

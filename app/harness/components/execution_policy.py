"""ExecutionHarness 执行策略辅助模块。

负责把工具 schema、步骤级别上限和全局请求超时合并成统一执行策略，供工具
执行器在运行时判断超时预算、重试次数与失败分类。
"""

from __future__ import annotations

from app.agents.tools.base import ToolExecutionError
from app.agents.tools.registry import ToolRegistry
from app.core.config import Settings
from app.harness.models import ContextBundle, ExecutionPolicy

_STEP_TIMEOUT_BUDGETS = {
    's1': 12000,
    's2': 18000,
    's3': 25000,
    's4': 30000,
    's4r': 30000,
    'review_artifact': 20000,
    'finalize': 15000,
    'retrieve_evidence': 18000,
    'grounded_answer': 20000,
}

_STEP_RETRY_CAPS = {
    's1': 1,
    's2': 2,
    's3': 2,
    's4': 2,
    's4r': 2,
    'review_artifact': 1,
    'finalize': 1,
    'retrieve_evidence': 2,
    'grounded_answer': 1,
}


class ExecutionPolicyResolver:
    """解析工具执行阶段的重试与超时策略。"""

    def __init__(self, registry: ToolRegistry, settings: Settings) -> None:
        """初始化策略解析器。"""

        self.registry = registry
        self.settings = settings

    def resolve(
        self,
        name: str,
        context_bundle: ContextBundle,
        *,
        failure_action: str | None = None,
    ) -> ExecutionPolicy:
        """综合工具 schema 与步骤上限生成执行策略。"""

        schema = self.registry.describe(name)
        step_id = context_bundle.step_id
        schema_total_attempts = max(1, int(schema.retry_policy.max_attempts) + 1)
        return ExecutionPolicy(
            tool_name=name,
            step_id=step_id,
            max_attempts=max(1, min(schema_total_attempts, _STEP_RETRY_CAPS.get(step_id, schema_total_attempts))),
            timeout_budget_ms=self.timeout_budget_ms(schema.timeout_ms, _STEP_TIMEOUT_BUDGETS.get(step_id)),
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown_ms=60000,
            failure_action=failure_action or 'abort',
        )

    def timeout_budget_ms(self, tool_timeout_ms: int, runtime_timeout_ms: int | None = None) -> int:
        """计算最终生效的超时预算。"""

        request_budget_ms = max(1000, int(self.settings.request_timeout_seconds * 1000))
        candidates = [value for value in (tool_timeout_ms, request_budget_ms, runtime_timeout_ms) if value and value > 0]
        return min(candidates) if candidates else 30000

    def should_retry(self, exc: ToolExecutionError, *, attempt_index: int, max_attempts: int) -> bool:
        """根据错误类型与剩余尝试次数判断是否允许重试。"""

        if attempt_index + 1 >= max_attempts:
            return False
        if exc.error_type in {'timeout_error', 'dependency_error', 'retryable_error'}:
            return True
        return exc.default_action == 'retry'

    def derive_retry_count(self, exc: ToolExecutionError) -> int:
        """从错误详情中提取已发生的重试次数。"""

        details_retry = exc.details.get('retry_count') if isinstance(exc.details, dict) else None
        try:
            return max(0, int(details_retry or 0))
        except (TypeError, ValueError):
            return 0

    def failure_category(self, exc: ToolExecutionError) -> str:
        """把工具错误映射成运行时使用的失败类别。"""

        if exc.error_type == 'timeout_error':
            return 'timeout'
        if exc.error_type == 'dependency_error':
            return 'dependency'
        if exc.error_type in {'validation_error', 'permission_error'}:
            return 'input_or_policy'
        if exc.default_action in {'fallback', 'degrade', 'skip_with_gap'}:
            return 'recoverable'
        return 'fatal'

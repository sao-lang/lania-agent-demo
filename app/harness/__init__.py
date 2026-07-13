"""Harness Runtime 公共导出。

这里改为懒加载，避免在 import `app.harness.models` 之类的轻量子模块时，
被 `__init__` 提前拉起 `context/execution` 等重依赖并形成循环导入。
"""

from __future__ import annotations

from importlib import import_module

_EXPORT_MAP = {
    'BoundedLocalReActRuntime': ('app.harness.react_runtime', 'BoundedLocalReActRuntime'),
    'CompressionEngine': ('app.harness.compression', 'CompressionEngine'),
    'ContextBundle': ('app.harness.models', 'ContextBundle'),
    'ContextHarness': ('app.harness.context', 'ContextHarness'),
    'ContextOptimizationResult': ('app.harness.context', 'ContextOptimizationResult'),
    'ContextPolicy': ('app.harness.context_policy', 'ContextPolicy'),
    'ContextSourceType': ('app.harness.context_policy', 'ContextSourceType'),
    'EvaluationHarness': ('app.harness.evaluation', 'EvaluationHarness'),
    'ExecutionAttempt': ('app.harness.models', 'ExecutionAttempt'),
    'ExecutionHarness': ('app.harness.execution', 'ExecutionHarness'),
    'ExecutionPolicy': ('app.harness.models', 'ExecutionPolicy'),
    'ExecutionRuntimeSummary': ('app.harness.models', 'ExecutionRuntimeSummary'),
    'GuardrailDecision': ('app.harness.models', 'GuardrailDecision'),
    'GuardrailEngine': ('app.harness.guardrails', 'GuardrailEngine'),
    'GroundingBundle': ('app.harness.grounding', 'GroundingBundle'),
    'GroundingClaim': ('app.harness.grounding', 'GroundingClaim'),
    'GroundingEngine': ('app.harness.grounding', 'GroundingEngine'),
    'GroundingResult': ('app.harness.grounding', 'GroundingResult'),
    'PolicyDecision': ('app.harness.models', 'PolicyDecision'),
    'PolicyEngine': ('app.harness.policy', 'PolicyEngine'),
    'PromptBuilder': ('app.harness.prompting', 'PromptBuilder'),
    'PromptRenderResult': ('app.harness.prompting', 'PromptRenderResult'),
    'PromptTemplate': ('app.harness.prompting', 'PromptTemplate'),
    'RecoveryManager': ('app.harness.recovery', 'RecoveryManager'),
    'ReActState': ('app.harness.react_runtime', 'ReActState'),
    'ReActTurn': ('app.harness.react_runtime', 'ReActTurn'),
    'ReflectionHarness': ('app.harness.reflection', 'ReflectionHarness'),
    'SelectionEngine': ('app.harness.selection', 'SelectionEngine'),
    'StepType': ('app.harness.context_policy', 'StepType'),
    'TokenBudgetEngine': ('app.harness.budgeting', 'TokenBudgetEngine'),
    'ToolSandbox': ('app.harness.sandbox', 'ToolSandbox'),
    'ToolSandboxDecision': ('app.harness.sandbox', 'ToolSandboxDecision'),
    'ToolExecutionResult': ('app.harness.models', 'ToolExecutionResult'),
}


def __getattr__(name: str):
    """按需解析公共导出符号，避免包级别过早导入重模块。"""

    if name not in _EXPORT_MAP:
        raise AttributeError(name)
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    *_EXPORT_MAP.keys(),
]

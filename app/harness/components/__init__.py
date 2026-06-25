"""Harness 组件公共导出模块。

通过懒加载方式统一导出 context、policy、guardrail 和 execution 相关组件，
避免轻量模块导入时提前触发重依赖初始化，降低循环导入风险。该模块只负责
导出组织，不承担任何运行时业务逻辑。
"""

from __future__ import annotations

from importlib import import_module

_EXPORT_MAP = {
    'ContextOptimizationResult': ('app.harness.components.context_models', 'ContextOptimizationResult'),
    'ContextValueSerializer': ('app.harness.components.context_builders', 'ContextValueSerializer'),
    'QueryContextBuilder': ('app.harness.components.context_builders', 'QueryContextBuilder'),
    'TaskContextBuilder': ('app.harness.components.context_builders', 'TaskContextBuilder'),
    'ExecutionHooks': ('app.harness.components.execution_hooks', 'ExecutionHooks'),
    'ExecutionPolicyResolver': ('app.harness.components.execution_policy', 'ExecutionPolicyResolver'),
    'ExecutionRuntimeDependencies': ('app.harness.components.tool_executor', 'ExecutionRuntimeDependencies'),
    'FallbackHandler': ('app.harness.components.fallback_handler', 'FallbackHandler'),
    'GuardrailErrorRaiser': ('app.harness.components.guardrail_raiser', 'GuardrailErrorRaiser'),
    'GuardrailEvaluator': ('app.harness.components.guardrail_checks', 'GuardrailEvaluator'),
    'PolicyEvaluator': ('app.harness.components.policy_checks', 'PolicyEvaluator'),
    'PolicyProfile': ('app.harness.components.policy_profiles', 'PolicyProfile'),
    'PolicyProfileResolver': ('app.harness.components.policy_profiles', 'PolicyProfileResolver'),
    'PolicyProfileStore': ('app.harness.components.policy_profiles', 'PolicyProfileStore'),
    'ToolExecutor': ('app.harness.components.tool_executor', 'ToolExecutor'),
}


def __getattr__(name: str):
    """按需加载组件对象，减少包初始化时的耦合成本。"""

    if name not in _EXPORT_MAP:
        raise AttributeError(name)
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [*_EXPORT_MAP.keys()]

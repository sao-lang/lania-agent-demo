"""Harness Runtime 公共导出。

这里改为懒加载，避免在 import `app.harness.models` 之类的轻量子模块时，
被 `__init__` 提前拉起 `context/execution` 等重依赖并形成循环导入。
"""

from __future__ import annotations

from importlib import import_module

_EXPORT_MAP = {
    'ContextBundle': ('app.agent_platform.harness.models', 'ContextBundle'),
    'ExecutionAttempt': ('app.agent_platform.harness.models', 'ExecutionAttempt'),
    'ExecutionPolicy': ('app.agent_platform.harness.models', 'ExecutionPolicy'),
    'ExecutionRuntimeSummary': ('app.agent_platform.harness.models', 'ExecutionRuntimeSummary'),
    'GuardrailDecision': ('app.agent_platform.harness.models', 'GuardrailDecision'),
    'GuardrailEngine': ('app.agent_platform.harness.guardrails', 'GuardrailEngine'),
    'PolicyDecision': ('app.agent_platform.harness.models', 'PolicyDecision'),
    'PolicyEngine': ('app.agent_platform.harness.policy', 'PolicyEngine'),
    'PromptBuilder': ('app.agent_platform.harness.prompting', 'PromptBuilder'),
    'PromptRenderResult': ('app.agent_platform.harness.prompting', 'PromptRenderResult'),
    'PromptTemplate': ('app.agent_platform.harness.prompting', 'PromptTemplate'),
    'ToolSandbox': ('app.agent_platform.harness.sandbox', 'ToolSandbox'),
    'ToolSandboxDecision': ('app.agent_platform.harness.sandbox', 'ToolSandboxDecision'),
    'ToolExecutionResult': ('app.agent_platform.harness.models', 'ToolExecutionResult'),
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

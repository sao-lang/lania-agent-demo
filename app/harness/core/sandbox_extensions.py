"""Sandbox 深化扩展——Context Sandbox 与 Capability Sandbox。

在 ToolSandbox（工具隔离）的基础上，增加两层更细粒度的运行时约束：

1. **Context Sandbox**
   控制 step / sub-agent / tool 可访问的上下文范围，
   保证每个执行单元只拿到最小必要信息。

2. **Capability Sandbox**
   按 capability 类型设定默认约束，
   例如 knowledge 只查知识库、filesystem 只读工作区。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.harness.models import ContextBundle


# ── Context Sandbox ─────────────────────────────────────────────


@dataclass
class ContextAccessPolicy:
    """定义某个执行单元允许访问的上下文范围。"""

    allowed_state_keys: list[str] = field(default_factory=list)
    blocked_state_keys: list[str] = field(default_factory=list)
    allowed_evidence_sources: list[str] = field(default_factory=list)
    allow_artifact: bool = True
    allow_memory: bool = True
    max_memory_items: int = 0
    max_evidence_items: int = 0
    allow_session_data: bool = False
    sensitive_fields: list[str] = field(default_factory=list)


DEFAULT_TOOL_CONTEXT_POLICY = ContextAccessPolicy(
    allowed_state_keys=[],
    blocked_state_keys=['task.request.api_key', 'task.request.secret'],
    allow_artifact=False,
    allow_memory=True,
    max_memory_items=10,
    max_evidence_items=20,
    allow_session_data=False,
)

DEFAULT_SUB_AGENT_CONTEXT_POLICY = ContextAccessPolicy(
    allowed_state_keys=[],
    blocked_state_keys=['task.request.api_key', 'task.request.secret'],
    allow_artifact=False,
    allow_memory=False,
    max_evidence_items=10,
    allow_session_data=False,
)


class ContextSandboxDecision(BaseModel):
    """Context Sandbox 的决策结果。"""

    allowed: bool = True
    policy_name: str = 'default'
    reason: str = 'allowed'
    filtered_bundle: ContextBundle | None = None
    removed_notes: list[str] = Field(default_factory=list)


class ContextSandbox:
    """运行时上下文沙盒——保证执行单元只拿到最小必要信息。"""

    def __init__(self) -> None:
        self._policies: dict[str, ContextAccessPolicy] = {}
        self.register_policy('tool', DEFAULT_TOOL_CONTEXT_POLICY)
        self.register_policy('sub_agent', DEFAULT_SUB_AGENT_CONTEXT_POLICY)

    def register_policy(
        self, name: str, policy: ContextAccessPolicy
    ) -> None:
        self._policies[name] = policy

    def get_policy(
        self, name: str
    ) -> ContextAccessPolicy | None:
        return self._policies.get(name)

    def filter(
        self,
        bundle: ContextBundle,
        *,
        policy_name: str = 'tool',
        extra_keys: list[str] | None = None,
    ) -> ContextSandboxDecision:
        """裁剪 ContextBundle 使之符合指定策略。"""
        policy = self._policies.get(policy_name)
        if policy is None:
            return ContextSandboxDecision(
                allowed=False,
                policy_name=policy_name,
                reason=f'policy_not_found: {policy_name}',
            )

        removed: list[str] = []
        allowed_keys = list(policy.allowed_state_keys)
        if extra_keys:
            allowed_keys.extend(extra_keys)

        # 裁剪 state_slice
        blocked = policy.blocked_state_keys
        for key in list(bundle.state_slice.keys()):
            if allowed_keys and key not in allowed_keys:
                del bundle.state_slice[key]
                removed.append(f'state_slice.{key}')
            elif key in blocked:
                del bundle.state_slice[key]
                removed.append(f'state_slice.{key} (blocked)')

        # 裁剪 evidence_slice
        if policy.allowed_evidence_sources:
            bundle.evidence_slice = [
                item for item in bundle.evidence_slice
                if item.get('source') in policy.allowed_evidence_sources
            ]
        max_ev = policy.max_evidence_items
        if max_ev > 0 and len(bundle.evidence_slice) > max_ev:
            bundle.evidence_slice = bundle.evidence_slice[:max_ev]
            removed.append(f'evidence_slice: cut to {max_ev} items')

        # 裁剪 artifact_slice
        if not policy.allow_artifact:
            bundle.artifact_slice = None
            removed.append('artifact_slice: disabled by policy')

        # 裁剪 memory_slice
        if not policy.allow_memory:
            bundle.memory_slice = {}
            removed.append('memory_slice: disabled by policy')
        elif policy.max_memory_items > 0:
            max_m = policy.max_memory_items
            keys = list(bundle.memory_slice.keys())[:max_m]
            bundle.memory_slice = {k: bundle.memory_slice[k] for k in keys}
            removed.append(f'memory_slice: cut to {max_m} keys')

        bundle.dropped_context_notes.extend(removed)

        return ContextSandboxDecision(
            allowed=True,
            policy_name=policy_name,
            reason='filtered',
            filtered_bundle=bundle,
            removed_notes=removed,
        )


# ── Capability Sandbox ──────────────────────────────────────────


@dataclass
class CapabilityConstraint:
    """某类 capability 的默认约束。"""

    capability_name: str
    allow_network: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    allowed_path_prefixes: list[str] = field(default_factory=list)
    max_concurrency: int = 1
    allow_write: bool = False
    timeout_ms: int = 30000


DEFAULT_CAPABILITY_CONSTRAINTS: list[CapabilityConstraint] = [
    CapabilityConstraint(
        capability_name='knowledge',
        allow_network=True,
        allowed_domains=['*'],
        allow_write=False,
        timeout_ms=20000,
    ),
    CapabilityConstraint(
        capability_name='filesystem',
        allow_network=False,
        allowed_path_prefixes=['/tmp', '/data/uploads'],
        allow_write=True,
        timeout_ms=10000,
    ),
    CapabilityConstraint(
        capability_name='http',
        allow_network=True,
        allowed_domains=['api.github.com', 'api.gitlab.com'],
        allow_write=False,
        timeout_ms=30000,
    ),
    CapabilityConstraint(
        capability_name='sql',
        allow_network=False,
        allow_write=False,
        timeout_ms=15000,
    ),
    CapabilityConstraint(
        capability_name='shell',
        allow_network=False,
        allow_write=True,
        max_concurrency=0,
        timeout_ms=30000,
    ),
]


class CapabilitySandbox:
    """按 capability 类型设定默认约束。"""

    def __init__(self) -> None:
        self._constraints: dict[str, CapabilityConstraint] = {
            c.capability_name: c
            for c in DEFAULT_CAPABILITY_CONSTRAINTS
        }

    def register_constraint(
        self, constraint: CapabilityConstraint
    ) -> None:
        self._constraints[constraint.capability_name] = constraint

    def get_constraint(
        self, capability_name: str
    ) -> CapabilityConstraint | None:
        return self._constraints.get(capability_name)

    def check(
        self,
        capability_name: str,
        *,
        domain: str | None = None,
        path: str | None = None,
        is_write: bool = False,
    ) -> tuple[bool, str]:
        """检查一次 capability 调用是否允许。"""
        constraint = self._constraints.get(capability_name)
        if constraint is None:
            return (False, f'unknown capability: {capability_name}')

        if domain and not constraint.allow_network:
            return (False, f'{capability_name}: network disabled')
        domains = constraint.allowed_domains
        if domain and domains and '*' not in domains:
            if not any(domain.endswith(d) for d in domains):
                return (False, f'{capability_name}: domain not allowed')

        if path and constraint.allowed_path_prefixes:
            if not any(path.startswith(p) for p in constraint.allowed_path_prefixes):
                return (False, f'{capability_name}: path not allowed')

        if is_write and not constraint.allow_write:
            return (False, f'{capability_name}: write disabled')

        if constraint.max_concurrency == 0:
            return (False, f'{capability_name}: concurrency disabled')

        return (True, 'allowed')
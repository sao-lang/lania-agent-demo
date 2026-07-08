"""Sandbox Execute Capability 模块。

提供沙盒命令执行能力，支持三级安全策略（sandboxed / restricted / standard）。
"""

from app.capabilities.sandbox_execute.base import (
    CommandExecutionRequest,
    CommandExecutionResult,
    CommandSecurityPolicy,
    SandboxExecuteCapability,
    build_sandboxed_policy,
    build_restricted_policy,
    build_standard_policy,
)
from app.capabilities.sandbox_execute.service import LocalSandboxExecuteCapability

__all__ = [
    "SandboxExecuteCapability",
    "CommandSecurityPolicy",
    "CommandExecutionRequest",
    "CommandExecutionResult",
    "LocalSandboxExecuteCapability",
    "build_sandboxed_policy",
    "build_restricted_policy",
    "build_standard_policy",
]
"""Harness 层公共数据模型。

负责定义上下文组装和统一执行入口之间共享的结构化对象，避免运行时再次退回到松散 dict。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextBundle(BaseModel):
    """进入单个步骤前的最小上下文切片。"""

    step_id: str
    objective: str
    state_slice: dict[str, Any] = Field(default_factory=dict)
    evidence_slice: list[dict[str, Any]] = Field(default_factory=list)
    artifact_slice: dict[str, Any] | None = None
    memory_slice: dict[str, Any] = Field(default_factory=dict)
    tool_options: list[str] = Field(default_factory=list)
    token_budget: int = Field(default=0, ge=0)


class ToolExecutionResult(BaseModel):
    """统一的工具执行结果摘要。"""

    tool_name: str
    status: str
    failure_category: str | None = None
    selected_action: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    timeout_budget_ms: int = Field(default=0, ge=0)
    sandbox_mode: str = 'inline'
    cost: float | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    data: dict[str, Any] | None = None
    trace_id: str


class ExecutionPolicy(BaseModel):
    """统一的执行治理策略。"""

    tool_name: str
    step_id: str
    max_attempts: int = Field(default=1, ge=1, le=5)
    timeout_budget_ms: int = Field(default=30000, ge=1)
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=20)
    circuit_breaker_cooldown_ms: int = Field(default=60000, ge=0, le=300000)
    failure_action: str = 'abort'


class ExecutionAttempt(BaseModel):
    """单次执行尝试的摘要。"""

    attempt_index: int = Field(default=0, ge=0)
    status: str = 'ok'
    latency_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_type: str | None = None


class ExecutionRuntimeSummary(BaseModel):
    """一次工具执行在 runtime 层的统一摘要。"""

    tool_name: str
    step_id: str
    status: str
    selected_action: str
    failure_category: str | None = None
    retry_count: int = Field(default=0, ge=0)
    timeout_budget_ms: int = Field(default=0, ge=0)
    sandbox_mode: str = 'inline'
    circuit_breaker_open: bool = False
    used_fallback: bool = False
    attempts: list[ExecutionAttempt] = Field(default_factory=list)
    trace_id: str


class GuardrailDecision(BaseModel):
    """统一的 guardrail 决策结果。"""

    allowed: bool
    stage: str
    code: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    """统一的策略决策结果。"""

    allowed: bool
    stage: str
    policy_name: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)

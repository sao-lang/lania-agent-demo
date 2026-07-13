"""Agent 工具基础协议模块。

负责定义工具 schema、运行时上下文、统一错误类型和注册协议，是任务工具层最底下那层公共约定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.agents.memory import TaskMemory
from app.core.config import Settings
from app.harness.model_router import ModelRouter
from app.rag.observability import TraceRecorder
from app.services.state import InMemoryState

ToolErrorType = Literal[
    'retryable_error',
    'validation_error',
    'dependency_error',
    'permission_error',
    'timeout_error',
    'fatal_error',
]
ToolFallbackAction = Literal['retry', 'fallback', 'degrade', 'skip_with_gap', 'abort']
ToolRiskLevel = Literal['low', 'medium', 'high']
ToolSandboxMode = Literal['inline', 'thread_isolated', 'process_isolated']


class ToolRetryPolicy(BaseModel):
    """工具重试策略。"""

    max_attempts: int = Field(default=0, ge=0, le=5)
    backoff_ms: int = Field(default=0, ge=0, le=60000)


class ToolErrorDefinition(BaseModel):
    """工具错误定义。"""

    code: str
    error_type: ToolErrorType
    default_action: ToolFallbackAction
    description: str


class ToolSchema(BaseModel):
    """统一对外暴露的工具 schema。"""

    name: str
    version: str = 'v1'
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_codes: list[ToolErrorDefinition] = Field(default_factory=list)
    timeout_ms: int = Field(default=30000, ge=0)
    retry_policy: ToolRetryPolicy = Field(default_factory=ToolRetryPolicy)
    trace_fields: list[str] = Field(default_factory=list)
    risk_level: ToolRiskLevel = 'low'
    sandbox_mode: ToolSandboxMode = 'inline'


class ToolMessage(BaseModel):
    """统一的工具 warning / error 消息。"""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolOutputEnvelope(BaseModel):
    """统一的工具输出 envelope。"""

    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[ToolMessage] = Field(default_factory=list)
    errors: list[ToolMessage] = Field(default_factory=list)


class ToolExecutionError(Exception):
    """统一封装工具执行错误，方便 workflow 按错误类型做分层处理。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        error_type: ToolErrorType,
        default_action: ToolFallbackAction,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化统一工具错误对象。"""

        self.code = code
        self.message = message
        self.error_type = error_type
        self.default_action = default_action
        self.details = details or {}
        super().__init__(message)


@dataclass
class ToolContext:
    """工具运行时依赖。

    这里把状态存储、检索能力、trace 和记忆服务这些常用依赖一起传进来，工具实现就不用再自己到处找。
    """

    state: InMemoryState
    retrieval: Any
    trace: TraceRecorder
    task_memory: TaskMemory
    settings: Settings
    llm: Any | None = None
    vector_store: Any | None = None
    deps: dict[str, Any] | None = None
    services: dict[str, Any] | None = None
    task_id: str | None = None
    step_name: str | None = None
    tool_call_id: str | None = None
    run_budget: Any | None = None
    model_router: ModelRouter | None = None
    # ── 文件级指令（由 ExecutionHarness 在阶段 2 注入） ──
    file_instructions: list[Any] | None = None

    def __getattr__(self, name: str) -> Any:
        if name == 'deps' or name.startswith('__'):
            raise AttributeError(name)
        if self.deps is not None and name in self.deps:
            return self.deps[name]
        raise AttributeError(f"'ToolContext' has no attribute '{name}'")


class AgentTool(Protocol):
    """所有工具都要遵循的统一协议。"""

    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    version: str
    timeout_ms: int
    retry_policy: ToolRetryPolicy
    trace_fields: list[str]

    def run(self, payload: Any, context: ToolContext) -> BaseModel:
        """执行工具并返回结构化输出对象。"""
        ...

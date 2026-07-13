"""大脑层数据模型。

定义意图识别、模式路由、步骤执行和安全策略的核心数据结构。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 意图识别
# ──────────────────────────────────────────────


class KnowledgeSource(str, Enum):
    """知识来源类型。"""
    INTERNAL_LLM = "internal_llm"
    RAG = "rag"
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"
    CALCULATOR = "calculator"
    CODE_REPO = "code_repo"
    DATABASE = "database"
    SANDBOX_EXEC = "sandbox_exec"
    SHELL_CMD = "shell_cmd"


class Complexity(str, Enum):
    """问题复杂度。"""
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class RiskLevel(str, Enum):
    """操作风险等级。"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SuggestedMode(str, Enum):
    """建议执行模式——决定交互基调，而非安全门控。"""
    CHAT = "chat"
    AUTOPILOT = "autopilot"
    PLAN = "plan"
    PLAN_CONFIRM = "plan_confirm"


class IntentDecision(BaseModel):
    """意图识别的结构化结果。

    注意：不包含 needs_consent 字段。
    确认是步骤级行为，由 StepExecutor 根据"步骤风险 + 当前 mode"动态决定。
    """
    complexity: Complexity
    suggested_sources: list[KnowledgeSource] = Field(default_factory=list)
    suggested_mode: SuggestedMode = SuggestedMode.CHAT
    needs_planning: bool = False
    risk_level: RiskLevel = RiskLevel.LOW
    confidence: float = 0.5
    reasoning: str = ""
    matched_capabilities: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────
# 模式路由
# ──────────────────────────────────────────────


class RouteContext(BaseModel):
    """模式路由上下文。"""
    user_prefers_confirmation: bool = False
    tool_count: int = 0
    has_destructive_operations: bool = False


class RouteResult(BaseModel):
    """模式路由结果。"""
    mode: SuggestedMode
    upgrade_reason: str = ""


# ──────────────────────────────────────────────
# 步骤执行
# ──────────────────────────────────────────────


class ToolCall(BaseModel):
    """单次工具调用。"""
    id: str = ""
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ClientExecutionResult(BaseModel):
    """客户端命令执行结果。"""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    truncated: bool = False


class ConsentScope(str, Enum):
    """记住用户选择的范围。"""
    NONE = "none"
    SESSION = "session"
    PERSISTENT = "persistent"


class ConsentResponse(BaseModel):
    """用户确认响应。"""
    action: Literal["approve", "deny"]
    remember: ConsentScope = ConsentScope.NONE


class ConsentRecord(BaseModel):
    """持久化的用户确认记录。"""
    user_id: str
    tool_name: str
    scope: ConsentScope
    granted_at: datetime = Field(default_factory=datetime.now)

    def is_valid(self) -> bool:
        """判断记录是否仍有效。session 级有效期到会话结束，persistent 持续有效。"""
        return self.scope in (ConsentScope.SESSION, ConsentScope.PERSISTENT)


# ──────────────────────────────────────────────
# 安全策略
# ──────────────────────────────────────────────


class CheckpointType(str, Enum):
    """安全策略检查点类型。"""
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    PRE_TOOL_OUTPUT_TO_LLM = "pre_tool_output_to_llm"


class SafetyDecision(BaseModel):
    """安全策略决策结果。"""
    allowed: bool = True
    level: str = "pass"  # "pass" | "warn" | "block"
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class SafetyContext(BaseModel):
    """安全策略的输入上下文。"""
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    execution_target: str = "server"
    session_history: list[str] = Field(default_factory=list)
    user_id: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

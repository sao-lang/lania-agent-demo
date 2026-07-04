"""Agent 对话模型模块。

定义 Agent 对话的请求、响应、事件流和模式/能力相关模型。
这些模型位于 API、AgentService 和 CLI/Web 之间的共享边界。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# 执行模式
# ──────────────────────────────────────────────

AgentMode = Literal["chat", "plan", "autopilot"]


# ──────────────────────────────────────────────
# SSE 事件类型
# ──────────────────────────────────────────────

AgentEventType = Literal[
    "intent",           # 意图识别结果
    "plan",             # 生成计划 (plan 模式)
    "plan_confirmed",   # 用户确认计划
    "step_start",       # 步骤开始
    "step_end",         # 步骤结束
    "tool_call",        # 工具调用
    "tool_result",      # 工具结果
    "delta",            # 流式文本增量
    "completed",        # 完成
    "error",            # 错误
    "ask_user",         # 询问用户 (autopilot 模式)
]


class AgentEvent(BaseModel):
    """Agent 执行事件，用于 SSE 流式推送。"""

    type: AgentEventType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)

    @classmethod
    def intent(cls, capability: str, confidence: float) -> "AgentEvent":
        return cls(
            type="intent",
            data={"capability": capability, "confidence": confidence},
        )

    @classmethod
    def plan(cls, steps: list[dict]) -> "AgentEvent":
        return cls(type="plan", data={"steps": steps})

    @classmethod
    def step_start(
        cls, step_id: int, name: str, description: str = "",
    ) -> "AgentEvent":
        return cls(
            type="step_start",
            data={
                "step_id": step_id, "name": name, "description": description,
            },
        )

    @classmethod
    def step_end(cls, step_id: int, status: str) -> "AgentEvent":
        return cls(
            type="step_end",
            data={"step_id": step_id, "status": status},
        )

    @classmethod
    def tool_call(cls, tool: str, args: dict | None = None) -> "AgentEvent":
        return cls(
            type="tool_call",
            data={"tool": tool, "args": args or {}},
        )

    @classmethod
    def tool_result(
        cls, tool: str, status: str = "success", duration_ms: int = 0,
    ) -> "AgentEvent":
        return cls(
            type="tool_result",
            data={"tool": tool, "status": status, "duration_ms": duration_ms},
        )

    @classmethod
    def delta(cls, content: str) -> "AgentEvent":
        return cls(type="delta", data={"content": content})

    @classmethod
    def completed(
        cls, task_id: str | None = None, duration_ms: int = 0,
    ) -> "AgentEvent":
        return cls(
            type="completed",
            data={"task_id": task_id, "duration_ms": duration_ms},
        )

    @classmethod
    def error(cls, message: str) -> "AgentEvent":
        return cls(type="error", data={"message": message})

    @classmethod
    def ask_user(cls, question: str) -> "AgentEvent":
        return cls(type="ask_user", data={"question": question})


# ──────────────────────────────────────────────
# 请求 / 响应
# ──────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    """统一的 Agent 对话请求。"""

    message: str = Field(min_length=1)
    mode: AgentMode = "chat"
    session_id: str | None = None
    capability: str | None = None          # null = 自动识别
    collection_name: str = "default"
    agent_name: str | None = None
    model: str | None = None
    stream: bool = True
    mcp_config: dict | None = None         # MCP 配置透传


class AgentCommandRequest(BaseModel):
    """一次性命令请求 (非流式)。"""

    message: str = Field(min_length=1)
    mode: AgentMode = "chat"
    collection_name: str = "default"
    capability: str | None = None
    model: str | None = None
    mcp_config: dict | None = None


class AgentCommandResponse(BaseModel):
    """一次性命令响应。"""

    answer: str
    capability: str
    task_id: str | None = None
    duration_ms: int = 0
    mode: AgentMode = "chat"


# ──────────────────────────────────────────────
# 能力定义
# ──────────────────────────────────────────────

class CapabilityInfo(BaseModel):
    """对外暴露的能力信息。"""

    name: str
    display_name: str
    description: str
    enabled: bool = True
    requires: list[str] = Field(default_factory=list)
    is_default: bool = False


# ──────────────────────────────────────────────
# 计划相关
# ──────────────────────────────────────────────

class PlanStep(BaseModel):
    """计划中的一个步骤。"""

    step_id: int
    name: str
    description: str = ""
    tool: str | None = None


class Plan(BaseModel):
    """执行计划。"""

    steps: list[PlanStep]
    summary: str = ""


class PlanConfirmRequest(BaseModel):
    """用户确认/拒绝计划的请求。"""

    session_id: str
    confirmed: bool
    session_token: str | None = None


# ──────────────────────────────────────────────
# 意图匹配结果
# ──────────────────────────────────────────────

class IntentMatch(BaseModel):
    """意图匹配结果。"""

    capability: str
    confidence: float
    matched_keywords: list[str] = Field(default_factory=list)

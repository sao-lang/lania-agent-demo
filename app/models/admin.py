"""管理配置模型模块。

LLM 配置、Skill、Agent 定义、提示词、MCP、系统设置等管理面共享的模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


# ── LLM 配置 ────────────────────────────────

class LlmProviderConfig(BaseModel):
    """LLM Provider 配置。"""
    name: str
    display_name: str = ""
    provider_type: str = "openai"  # openai, anthropic, azure, ollama, custom
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None
    is_active: bool = False
    models: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class LlmProviderStatus(BaseModel):
    """LLM Provider 连接状态。"""
    name: str
    model: str
    status: Literal["ok", "error", "untested"] = "untested"
    error: str | None = None
    latency_ms: int | None = None


# ── Skill 定义 ──────────────────────────────

class SkillRule(BaseModel):
    """Skill 子规则。"""
    id: str = Field(default_factory=lambda: f"skr-{uuid4().hex[:12]}")
    skill_id: str
    name: str                           # 如 "00-base"
    apply_to: str = "**/*"              # 适用范围 glob
    content: str = ""                   # 规则正文（markdown）
    order: int = 0                      # 排序序号
    created_at: datetime = Field(default_factory=datetime.now)


class SkillDefinition(BaseModel):
    """Skill 定义。"""
    id: str = Field(default_factory=lambda: f"sk-{uuid4().hex[:12]}")
    name: str
    version: int = 1
    description: str = ""
    instructions: str = ""
    task_types: list[str] = Field(default_factory=list)
    tools: list[str] | None = None
    source: Literal["builtin", "file", "api"] = "api"
    rules: list[SkillRule] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class SkillCreateRequest(BaseModel):
    """创建/更新 Skill 的请求体（不含 id，由服务端生成）。"""
    name: str
    description: str = ""
    instructions: str = ""
    task_types: list[str] = Field(default_factory=list)
    tools: list[str] | None = None
    source: Literal["builtin", "file", "api"] = "api"
    rules: list[SkillRuleCreate] = Field(default_factory=list)


class SkillRuleCreate(BaseModel):
    """创建 Skill 子规则的请求体。"""
    name: str
    apply_to: str = "**/*"
    content: str = ""
    order: int = 0


# ── Agent 定义 ──────────────────────────────

class AgentDefinition(BaseModel):
    """Agent 定义（类似 Copilot CLI 的 custom agent）。"""
    id: str = Field(default_factory=lambda: f"agt-{uuid4().hex[:12]}")
    name: str
    display_name: str = ""
    description: str = ""
    instructions: str = ""
    skills: list[str] = Field(default_factory=list)
    allowed_tools: list[str] | None = None
    model: str | None = None
    temperature: float = 0.7
    max_turns: int = 10
    is_default: bool = False
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AgentCreateRequest(BaseModel):
    """创建/更新 Agent 的请求体（不含 id）。"""
    name: str
    display_name: str = ""
    description: str = ""
    instructions: str = ""
    skills: list[str] = Field(default_factory=list)
    allowed_tools: list[str] | None = None
    model: str | None = None
    temperature: float = 0.7
    max_turns: int = 10
    is_default: bool = False


# ── 提示词模板 ──────────────────────────────

class PromptTemplate(BaseModel):
    """提示词模板。"""
    id: str = Field(default_factory=lambda: f"prt-{uuid4().hex[:12]}")
    name: str
    description: str = ""
    template: str = ""
    variables: list[str] = Field(default_factory=list)
    is_builtin: bool = False
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class PromptCreateRequest(BaseModel):
    """创建/更新 Prompt 的请求体（不含 id）。"""
    name: str
    description: str = ""
    template: str = ""
    variables: list[str] = Field(default_factory=list)


# ── MCP 配置 ────────────────────────────────

class McpServerConfig(BaseModel):
    """MCP Server 配置。"""
    id: str = Field(default_factory=lambda: f"mcp-{uuid4().hex[:12]}")
    name: str
    server_type: Literal["url", "stdio"] = "url"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    enabled: bool = True
    status: str = "disconnected"        # connected | disconnected | error
    tools_count: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class McpServerCreateRequest(BaseModel):
    """创建/更新 MCP Server 的请求体（不含 id 和运行时状态）。"""
    name: str
    server_type: Literal["url", "stdio"] = "url"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    enabled: bool = True


# ── 系统设置 ────────────────────────────────

class SystemSettings(BaseModel):
    """运行时系统设置。"""
    default_collection: str = "default"
    default_top_k: int = 5
    default_model: str = "gpt-4o-mini"
    default_language: str = "zh-CN"
    enable_guardrails: bool = True
    enable_context_compression: bool = True
    enable_semantic_cache: bool = True
    enable_pii_redaction: bool = True
    max_tool_calls_per_step: int = 5


# ── 系统指令 ────────────────────────────────

class InstructionsUpdateRequest(BaseModel):
    """更新系统指令的请求体。"""
    content: str = ""


class InstructionsResponse(BaseModel):
    """系统指令响应。"""
    content: str
    length: int
    source: str = "file"


# ── 文件指令 ────────────────────────────────

class FileInstructionCreate(BaseModel):
    """创建文件指令的请求体。"""
    name: str
    apply_to: str = "**/*"
    content: str = ""


class FileInstructionUpdate(BaseModel):
    """更新文件指令的请求体。"""
    apply_to: str | None = None
    content: str | None = None


class FileInstructionResponse(BaseModel):
    """文件指令响应。"""
    name: str
    apply_to: str
    content: str
    source: str = "file"


# ── Hook ─────────────────────────────────────

class HookCreateRequest(BaseModel):
    """创建 Hook 的请求体。"""
    name: str
    description: str = ""
    events: list[str] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class HookUpdateRequest(BaseModel):
    """更新 Hook 的请求体。"""
    description: str | None = None
    events: list[str] | None = None
    conditions: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None


class HookResponse(BaseModel):
    """Hook 响应。"""
    name: str
    description: str = ""
    events: list[str] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
"""管理配置模型模块。

LLM 配置、Skill、Agent 定义、提示词、MCP、系统设置等管理面共享的模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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

class SkillDefinition(BaseModel):
    """Skill 定义。"""
    name: str
    description: str = ""
    instructions: str = ""
    task_types: list[str] = Field(default_factory=list)
    tools: list[str] | None = None
    source: Literal["builtin", "file", "api"] = "api"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Agent 定义 ──────────────────────────────

class AgentDefinition(BaseModel):
    """Agent 定义（类似 Copilot CLI 的 custom agent）。"""
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
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── 提示词模板 ──────────────────────────────

class PromptTemplate(BaseModel):
    """提示词模板。"""
    name: str
    description: str = ""
    template: str = ""
    variables: list[str] = Field(default_factory=list)
    is_builtin: bool = False
    version: int = 1
    updated_at: datetime = Field(default_factory=datetime.now)


# ── MCP 配置 ────────────────────────────────

class McpServerConfig(BaseModel):
    """MCP Server 配置。"""
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

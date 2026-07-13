"""标准化原语文件 Schema 模块。

定义所有定制化原语文件（.agent.md / .prompt.md / .instructions.md / SKILL.md）
的统一 Frontmatter Schema，支持 Pydantic 校验。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 通用 Frontmatter ────────────────────────

PrimitiveType = Literal[
    "instructions",
    "prompt",
    "skill",
    "agent",
    "hook",
    "mcp",
]


class PrimitiveFrontmatter(BaseModel):
    """所有原语文件的统一 Frontmatter Schema。

    所有字段均为可选——只提供特定类型所需的字段。
    """
    # 通用字段
    name: str | None = None
    description: str | None = None
    type: PrimitiveType | None = None

    # 文件指令
    apply_to: str | None = Field(default=None, alias="applyTo")

    # Prompt
    variables: list[str] | None = None

    # Agent
    display_name: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_turns: int | None = None
    allowed_tools: list[str] | None = None
    skills: list[str] | None = None
    is_default: bool | None = None

    # Hook
    events: list[str] | None = None
    conditions: dict[str, Any] | None = None

    # Skill
    task_types: list[str] | None = None
    tools: list[str] | None = None
    source: Literal["builtin", "file", "api"] | None = None

    # MCP
    server_type: Literal["url", "stdio"] | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    enabled: bool | None = None

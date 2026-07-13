"""系统指令管理器。

加载 .agents/AGENTS.md，始终注入 Agent 系统提示词。
支持多级继承：项目级 → Agent 级 → 请求级。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.admin import AgentDefinition


class InstructionsManager:
    """系统指令管理器。

    从 .agents/AGENTS.md 加载项目级系统指令，
    在 Agent 初始化时按优先级组装完整 System Prompt。
    """

    def __init__(self, agents_dir: str | Path = ".lania") -> None:
        self._agents_dir = Path(agents_dir)

    def load_project_instructions(self) -> str:
        """加载项目级系统指令（.agents/AGENTS.md）。"""
        path = self._agents_dir / "AGENTS.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def build_system_prompt(
        self,
        agent_def: AgentDefinition | None = None,
        extra_instructions: str = "",
    ) -> str:
        """组装完整系统提示词。

        优先级（高 → 低）：
        1. extra_instructions（请求级）
        2. agent_def.instructions（Agent 级）
        3. AGENTS.md（项目级）
        """
        parts: list[str] = []

        project_instructions = self.load_project_instructions()
        if project_instructions:
            parts.append(project_instructions)

        if agent_def and agent_def.instructions:
            parts.append(agent_def.instructions)

        if extra_instructions:
            parts.append(extra_instructions)

        return "\n\n".join(p for p in parts if p)

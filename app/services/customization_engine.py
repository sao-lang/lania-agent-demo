"""定制化原语引擎——统一加载和组装所有原语。

扫描 .agents/ 目录加载所有原语文件（Skills / Agents / Prompts / MCPs / Hooks / FileInstructions），
与各 Manager 同步，为会话构建完整上下文。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.admin import AgentDefinition
    from app.services.agent_def_manager import AgentDefManager
    from app.services.file_instruction_manager import (
        FileInstructionManager,
    )
    from app.services.mcp_manager import McpManager
    from app.services.prompt_manager import PromptManager
    from app.services.skill_manager import SkillManager
    from app.harness.hooks import EventBus


from app.services.instructions_manager import InstructionsManager


@dataclass
class SessionContext:
    """会话上下文——一次 Agent 会话所需的所有定制化信息。"""
    agent_def: AgentDefinition | None
    system_prompt: str
    extension_catalog: str
    allowed_tools: list[str] | None


class CustomizationEngine:
    """定制化原语引擎。

    职责：
    - 扫描 .agents/ 目录加载所有原语文件
    - 提供按类型、按名称的查询
    - 构建会话级系统提示词
    - 与各 Manager 同步（文件 → DB）
    """

    def __init__(
        self,
        agents_dir: str | Path,
        *,
        skill_manager: SkillManager | None = None,
        agent_def_manager: AgentDefManager | None = None,
        prompt_manager: PromptManager | None = None,
        mcp_manager: McpManager | None = None,
        event_bus: EventBus | None = None,
        file_instruction_manager: FileInstructionManager | None = None,
        instructions_manager: InstructionsManager | None = None,
        settings: Any | None = None,
    ) -> None:
        self._agents_dir = Path(agents_dir)
        self._skill_manager = skill_manager
        self._agent_def_manager = agent_def_manager
        self._prompt_manager = prompt_manager
        self._mcp_manager = mcp_manager
        self._event_bus = event_bus
        self._file_inst_mgr = file_instruction_manager
        self._inst_mgr = instructions_manager or InstructionsManager(agents_dir)
        self._settings = settings

    # ── 生命周期 ──────────────────────────────

    async def initialize(self) -> None:
        """应用启动时初始化——扫描文件并同步到各 Manager。"""
        if self._settings:
            auto_import_skills = getattr(self._settings, "auto_import_skills", True)
            auto_import_agents = getattr(self._settings, "auto_import_agents", True)
            auto_import_prompts = getattr(self._settings, "auto_import_prompts", True)
            auto_connect_mcp = getattr(self._settings, "auto_connect_mcp", True)
            enable_file_hooks = getattr(self._settings, "enable_file_hooks", True)
            enable_file_instructions = getattr(self._settings, "enable_file_instructions", True)
        else:
            auto_import_skills = True
            auto_import_agents = True
            auto_import_prompts = True
            auto_connect_mcp = True
            enable_file_hooks = True
            enable_file_instructions = True

        if auto_import_skills:
            await self._sync_skills()
        if auto_import_agents:
            await self._sync_agents()
        if auto_import_prompts:
            await self._sync_prompts()
        if auto_connect_mcp:
            await self._sync_mcp_servers()
        if enable_file_hooks:
            self._sync_hooks()
        if enable_file_instructions and self._file_inst_mgr:
            self._file_inst_mgr.load_all()

    async def build_session_context(
        self,
        agent_name: str | None = None,
    ) -> SessionContext:
        """为会话构建完整上下文。"""
        # 1. 解析 Agent 定义
        agent_def: AgentDefinition | None = None
        if agent_name and self._agent_def_manager:
            agent_def = await self._agent_def_manager.get_by_name(agent_name)
        if not agent_def and self._agent_def_manager:
            agent_def = await self._agent_def_manager.get_default()

        # 2. 构建系统提示词
        system_prompt = self._inst_mgr.build_system_prompt(agent_def)

        # 3. 构建扩展清单
        catalog = ""
        if self._skill_manager and agent_def:
            catalog = await self._skill_manager.build_routing_table(agent_def.skills)

        # 4. 解析工具白名单
        allowed_tools = agent_def.allowed_tools if agent_def else None

        return SessionContext(
            agent_def=agent_def,
            system_prompt=system_prompt,
            extension_catalog=catalog,
            allowed_tools=allowed_tools,
        )

    # ── 文件同步 ──────────────────────────────

    async def _sync_skills(self) -> None:
        """扫描 .agents/skills/ 目录，同步到 SkillManager。"""
        if not self._skill_manager:
            return
        skills_dir = self._agents_dir / "skills"
        if not skills_dir.exists():
            return
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                existing = await self._skill_manager.get_by_name(skill_dir.name)
                if existing is None:
                    await self._skill_manager.import_from_dir(str(skill_dir))

    async def _sync_agents(self) -> None:
        """扫描 .agents/agents/ 目录，同步到 AgentDefManager。"""
        if not self._agent_def_manager:
            return
        agents_dir = self._agents_dir / "agents"
        if not agents_dir.exists():
            return
        for fpath in agents_dir.glob("*.agent.md"):
            existing = await self._agent_def_manager.get_by_name(fpath.stem)
            if existing is None:
                await self._agent_def_manager.import_from_file(str(fpath))

    async def _sync_prompts(self) -> None:
        """扫描 .agents/prompts/ 目录，同步到 PromptManager。"""
        if not self._prompt_manager:
            return
        prompts_dir = self._agents_dir / "prompts"
        if not prompts_dir.exists():
            return
        for fpath in prompts_dir.glob("*.prompt.md"):
            existing = await self._prompt_manager.get_by_name(fpath.stem)
            if existing is None:
                await self._prompt_manager.import_from_file(str(fpath))

    async def _sync_mcp_servers(self) -> None:
        """加载并连接 MCP Server。"""
        if not self._mcp_manager:
            return
        config_path = self._agents_dir / "mcp-servers.json"
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text(encoding="utf-8"))
        await self._mcp_manager.connect(config)

    def _sync_hooks(self) -> None:
        """加载文件 Hook 并注册到 EventBus。"""
        hooks_dir = self._agents_dir / "hooks"
        if not hooks_dir.exists() or not self._event_bus:
            return
        from app.services.hook_actions import HookActionEngine
        from app.services.hook_adapter import HookRuntimeAdapter
        from app.services.hook_loader import FileHookLoader

        loader = FileHookLoader()
        for file_hook in loader.load_all(hooks_dir):
            adapter = HookRuntimeAdapter(file_hook, HookActionEngine())
            for event in file_hook.events:
                self._event_bus.register(adapter, event)

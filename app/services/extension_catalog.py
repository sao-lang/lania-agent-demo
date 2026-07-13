"""扩展清单模块。

给大模型提供一份轻量级"菜单"（名字+描述），大模型按需调用 load_extension
加载完整内容。Prompt 不在此清单中，由系统内部处理。

核心设计：
- 清单（Catalog）：~50 tokens/扩展，始终在系统提示词中
- 加载（load_extension）：大模型按需调用，加载完整扩展内容
- 规则（load_rule）：Skill 加载后，大模型根据路由表按需加载具体规则
"""

from __future__ import annotations

from typing import Any

from app.services.agent_def_manager import AgentDefManager
from app.services.mcp_manager import McpManager
from app.services.skill_manager import SkillManager


class ExtensionCatalog:
    """扩展清单——给大模型的"菜单"。

    只包含名字+描述，不包含完整内容。大模型根据需要调用工具加载。
    """

    def __init__(
        self,
        skill_manager: SkillManager | None = None,
        agent_def_manager: AgentDefManager | None = None,
        mcp_manager: McpManager | None = None,
    ) -> None:
        self._skill_manager = skill_manager
        self._agent_def_manager = agent_def_manager
        self._mcp_manager = mcp_manager

    # ── 清单构建 ──────────────────────────────

    async def build_catalog(self, skill_names: list[str] | None = None) -> str:
        """构建扩展清单（轻量，~50 tokens/扩展）。

        Args:
            skill_names: 限定哪些 skill 进入清单。None = 全部。

        Returns:
            格式化的扩展清单字符串，注入系统提示词。
        """
        parts: list[str] = ["## 可用扩展\n"]

        # Skills
        if self._skill_manager:
            skills = await self._skill_manager.list()
            if skill_names:
                skill_name_set = set(skill_names)
                skills = [s for s in skills if s.name in skill_name_set]
            if skills:
                parts.append("### Skills")
                for s in skills:
                    parts.append(f"- `{s.name}`: {s.description or '编码规则'}")
                parts.append("")

        # MCPs
        if self._mcp_manager:
            try:
                mcp_configs = await self._mcp_manager.list_servers_config()
            except Exception:
                mcp_configs = []
            if mcp_configs:
                parts.append("### MCP 工具")
                for m in mcp_configs:
                    parts.append(f"- `{m.name}`: {m.server_type} 连接")
                parts.append("")

        # Agents（可选的子 Agent 切换）
        if self._agent_def_manager:
            agents = await self._agent_def_manager.list()
            if agents:
                parts.append("### 子 Agent")
                for a in agents:
                    parts.append(f"- `{a.name}`: {a.description or 'Agent'}")
                parts.append("")

        parts.append(
            "使用 `load_extension(name, type)` 加载扩展的完整内容。"
            "type 可选: skill | mcp | agent"
        )

        return "\n".join(parts)

    # ── 按需加载 ──────────────────────────────

    async def load_extension(self, name: str, ext_type: str) -> str:
        """加载扩展的完整内容。

        Args:
            name: 扩展名称。
            ext_type: 扩展类型 (skill | mcp | agent)。

        Returns:
            扩展的完整内容字符串。
        """
        if ext_type == "skill":
            return await self._load_skill(name)
        elif ext_type == "mcp":
            return await self._load_mcp(name)
        elif ext_type == "agent":
            return await self._load_agent(name)
        else:
            return f"未知扩展类型: {ext_type}。可选: skill, mcp, agent"

    async def load_rule(self, skill_name: str, rule_name: str) -> str:
        """加载 Skill 的特定规则。

        Args:
            skill_name: Skill 名称。
            rule_name: 规则名称（如 "10-python"）。

        Returns:
            规则内容。
        """
        if self._skill_manager is None:
            return "Skill 管理器不可用"

        skill = await self._skill_manager.get_by_name(skill_name)
        if skill is None:
            return f"Skill '{skill_name}' 未找到"

        for rule in skill.rules:
            if rule.name == rule_name:
                return f"## Rule: {rule.name}\n{rule.content}"

        available = [r.name for r in skill.rules]
        return f"规则 '{rule_name}' 未找到。可用规则: {', '.join(available)}"

    # ── 内部实现 ──────────────────────────────

    async def _load_skill(self, name: str) -> str:
        if self._skill_manager is None:
            return "Skill 管理器不可用"

        skill = await self._skill_manager.get_by_name(name)
        if skill is None:
            return f"Skill '{name}' 未找到"

        # 返回 SKILL.md 的 instructions（路由表），让大模型决定加载哪些规则
        rule_names = [r.name for r in skill.rules]
        return (
            f"## Skill: {skill.name}\n"
            f"{skill.instructions}\n\n"
            f"可用规则: {', '.join(rule_names) if rule_names else '无'}\n\n"
            f"使用 `load_rule(skill_name=\"{name}\", rule_name=\"<规则名>\")` 加载具体规则。"
        )

    async def _load_mcp(self, name: str) -> str:
        if self._mcp_manager is None:
            return "MCP 管理器不可用"

        try:
            config = await self._mcp_manager.get_server(name)
            if config is None:
                # 尝试从配置列表查找
                configs = await self._mcp_manager.list_servers_config()
                for c in configs:
                    if c.name == name:
                        config = c
                        break
            if config is None:
                return f"MCP '{name}' 未找到"

            tools = await self._mcp_manager.get_or_connect(config.id)
            if not tools:
                return f"MCP '{name}' 已连接，但无可用工具"

            tool_list = "\n".join(
                f"- `{t.name}`: {t.description}" for t in tools
            )
            return f"## MCP: {name}\n已连接，可用工具:\n{tool_list}"
        except Exception as e:
            return f"MCP '{name}' 连接失败: {e}"

    async def _load_agent(self, name: str) -> str:
        if self._agent_def_manager is None:
            return "Agent 定义管理器不可用"

        agent = await self._agent_def_manager.get_by_name(name)
        if agent is None:
            return f"Agent '{name}' 未找到"

        return (
            f"## Agent: {agent.display_name or agent.name}\n"
            f"{agent.description or ''}\n\n"
            f"{agent.instructions or ''}"
        )

    # ── 工具注册 ──────────────────────────────

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """获取 load_extension 和 load_rule 的工具定义。

        用于注册到 ToolRegistry，让大模型可以调用。
        """
        return [
            {
                "name": "load_extension",
                "description": "加载扩展的完整内容。Skill 返回路由表（含可用规则列表），MCP 返回可用工具，Agent 返回指令。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "扩展名称，与清单中的名称一致",
                        },
                        "ext_type": {
                            "type": "string",
                            "enum": ["skill", "mcp", "agent"],
                            "description": "扩展类型",
                        },
                    },
                    "required": ["name", "ext_type"],
                },
            },
            {
                "name": "load_rule",
                "description": "加载 Skill 的具体规则文件。先调用 load_extension 获取路由表，再按需调用此方法加载具体规则。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Skill 名称",
                        },
                        "rule_name": {
                            "type": "string",
                            "description": "规则名称（如 10-python, 00-base）",
                        },
                    },
                    "required": ["skill_name", "rule_name"],
                },
            },
        ]
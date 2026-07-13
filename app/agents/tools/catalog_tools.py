"""扩展清单工具模块。

提供 load_extension 和 load_rule 两个工具，让大模型按需加载扩展内容。
这两个工具不直接修改系统状态，而是返回扩展内容供大模型阅读。
"""

from __future__ import annotations

from typing import Any

from app.agents.tools.base import Tool
from app.services.extension_catalog import ExtensionCatalog


class LoadExtensionTool(Tool):
    """大模型按需加载扩展的完整内容。

    Skill → 返回路由表（含可用规则列表），大模型再决定加载哪些规则
    MCP → 连接并返回可用工具列表
    Agent → 返回 Agent 指令
    """

    @property
    def name(self) -> str:
        return "load_extension"

    @property
    def description(self) -> str:
        return (
            "加载扩展的完整内容。Skill 返回路由表（含可用规则列表），"
            "MCP 返回可用工具，Agent 返回指令。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
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
        }

    def __init__(self, catalog: ExtensionCatalog) -> None:
        self._catalog = catalog

    async def execute(self, **kwargs: Any) -> str:
        return await self._catalog.load_extension(
            name=kwargs["name"],
            ext_type=kwargs["ext_type"],
        )


class LoadRuleTool(Tool):
    """大模型加载 Skill 的具体规则文件。

    先调用 load_extension 获取路由表，再按需调用此方法加载具体规则。
    """

    @property
    def name(self) -> str:
        return "load_rule"

    @property
    def description(self) -> str:
        return (
            "加载 Skill 的具体规则文件。"
            "先调用 load_extension 获取路由表，再按需调用此方法加载具体规则。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
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
        }

    def __init__(self, catalog: ExtensionCatalog) -> None:
        self._catalog = catalog

    async def execute(self, **kwargs: Any) -> str:
        return await self._catalog.load_rule(
            skill_name=kwargs["skill_name"],
            rule_name=kwargs["rule_name"],
        )
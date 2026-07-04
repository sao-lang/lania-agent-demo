"""Agent 定义管理模块。

管理自定义 Agent 定义的 CRUD。
Agent 定义 = 指令 + Skill 绑定 + 工具白名单 + LLM 配置。
"""

from __future__ import annotations

from datetime import datetime

from app.models.admin import AgentDefinition
from app.services.config_store import ConfigStore


class AgentDefManager:
    """Agent 定义管理器。"""

    _NAMESPACE = "agent_def"

    def __init__(self, config_store: ConfigStore) -> None:
        self._store = config_store

    async def list(self) -> list[AgentDefinition]:
        """列出所有 Agent 定义。"""
        items = self._store.list(self._NAMESPACE)
        agents: list[AgentDefinition] = []
        for item in items:
            if isinstance(item.value, dict):
                agents.append(AgentDefinition(**item.value))
        return agents

    async def get(self, name: str) -> AgentDefinition | None:
        """获取指定 Agent 定义。"""
        value = self._store.get(self._NAMESPACE, name)
        if value and isinstance(value, dict):
            return AgentDefinition(**value)
        return None

    async def create(self, agent: AgentDefinition) -> None:
        """创建 Agent 定义。"""
        agent.created_at = datetime.now()
        agent.updated_at = datetime.now()
        self._store.set(self._NAMESPACE, agent.name, agent.model_dump())

    async def update(self, name: str, agent: AgentDefinition) -> None:
        """更新 Agent 定义。"""
        agent.updated_at = datetime.now()
        self._store.set(self._NAMESPACE, name, agent.model_dump())

    async def delete(self, name: str) -> None:
        """删除 Agent 定义。"""
        self._store.delete(self._NAMESPACE, name)

    async def set_default(self, name: str) -> None:
        """设为默认 Agent。"""
        agents = await self.list()
        for a in agents:
            if a.is_default:
                a.is_default = False
                await self._store.set(
                    self._NAMESPACE, a.name, a.model_dump(),
                )
        agent = await self.get(name)
        if agent:
            agent.is_default = True
            await self._store.set(
                self._NAMESPACE, name, agent.model_dump(),
            )

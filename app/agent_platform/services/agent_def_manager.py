"""Agent 定义管理模块。

管理自定义 Agent 定义的 CRUD，支持版本控制。
Agent 定义 = 指令 + Skill 绑定 + 工具白名单 + LLM 配置。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4


from app.models.admin import AgentCreateRequest, AgentDefinition
from app.services.sqlite_store import SQLiteStateStore


class AgentDefManager:
    """Agent 定义管理器。

    使用 SQLiteStateStore 持久化，支持版本控制和默认 Agent 设置。
    """

    def __init__(self, persistence: SQLiteStateStore) -> None:
        self._persistence = persistence
        self._cache: dict[str, object] = {}

    def _invalidate_cache(self) -> None:
        self._cache.clear()

    # ── CRUD ──────────────────────────────────

    async def create(self, request: AgentCreateRequest) -> AgentDefinition:
        """创建 Agent 定义。"""
        now = datetime.now()
        agent = AgentDefinition(
            id=f"agt-{uuid4().hex[:12]}",
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            instructions=request.instructions,
            skills=request.skills,
            allowed_tools=request.allowed_tools,
            model=request.model,
            temperature=request.temperature,
            max_turns=request.max_turns,
            is_default=request.is_default,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._persistence.upsert_agent_def(agent.model_dump(mode="json"))
        self._invalidate_cache()
        return agent

    async def update(self, agent_id: str, request: AgentCreateRequest) -> AgentDefinition:
        """更新 Agent 定义（version 递增）。"""
        existing = await self.get(agent_id)
        if existing is None:
            raise ValueError(f"Agent '{agent_id}' not found")

        now = datetime.now()
        agent = AgentDefinition(
            id=agent_id,
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            instructions=request.instructions,
            skills=request.skills,
            allowed_tools=request.allowed_tools,
            model=request.model,
            temperature=request.temperature,
            max_turns=request.max_turns,
            is_default=request.is_default,
            version=existing.version + 1,
            created_at=existing.created_at,
            updated_at=now,
        )
        self._persistence.upsert_agent_def(agent.model_dump(mode="json"))
        self._invalidate_cache()
        return agent

    async def get(self, agent_id: str) -> AgentDefinition | None:
        """按 id 获取 Agent 定义。"""
        cache_key = f"id:{agent_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        payload = self._persistence.get_agent_def(agent_id)
        if payload is None:
            return None
        agent = AgentDefinition(**payload)
        self._cache[cache_key] = agent
        return agent

    async def get_by_name(self, name: str) -> AgentDefinition | None:
        """按 name 查找 Agent。"""
        cache_key = f"name:{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        agents = await self.list()
        for a in agents:
            if a.name == name:
                self._cache[cache_key] = a
                return a
        return None

    async def get_default(self) -> AgentDefinition | None:
        """获取默认 Agent。"""
        if "default" in self._cache:
            return self._cache["default"]  # type: ignore[return-value]

        agents = await self.list()
        for a in agents:
            if a.is_default:
                self._cache["default"] = a
                return a
        return None

    async def list(self) -> list[AgentDefinition]:
        """列出所有 Agent 定义。"""
        if "list" in self._cache:
            return self._cache["list"]  # type: ignore[return-value]

        payloads = self._persistence.list_agent_defs()
        agents = [AgentDefinition(**p) for p in payloads]
        self._cache["list"] = agents
        return agents

    async def delete(self, agent_id: str) -> None:
        """删除 Agent 定义。"""
        self._persistence.delete_agent_def(agent_id)
        self._invalidate_cache()

    async def set_default(self, agent_id: str) -> None:
        """设为默认 Agent。"""
        agents = await self.list()
        for a in agents:
            if a.is_default:
                a.is_default = False
                self._persistence.upsert_agent_def(a.model_dump(mode="json"))

        agent = await self.get(agent_id)
        if agent:
            agent.is_default = True
            self._persistence.upsert_agent_def(agent.model_dump(mode="json"))
        self._invalidate_cache()

    # ── 文件导入 ──────────────────────────────

    async def import_from_file(self, file_path: str) -> AgentDefinition:
        """从 .agent.md 文件导入 Agent 定义。

        Frontmatter 解析为 AgentDefinition 的元数据字段，
        正文 body 作为 instructions。
        """
        path = Path(file_path)
        if not path.exists():
            raise ValueError(f"File not found: {file_path}")

        frontmatter, body = self._parse_frontmatter(path.read_text(encoding="utf-8"))

        request = AgentCreateRequest(
            name=frontmatter.get("name", path.stem),
            display_name=frontmatter.get("display_name", ""),
            description=frontmatter.get("description", ""),
            instructions=body.strip(),
            skills=frontmatter.get("skills", []),
            allowed_tools=frontmatter.get("allowed_tools"),
            model=frontmatter.get("model"),
            temperature=frontmatter.get("temperature", 0.7),
            max_turns=frontmatter.get("max_turns", 10),
            is_default=frontmatter.get("is_default", False),
        )
        return await self.create(request)

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """解析 YAML frontmatter。"""
        from app.services.frontmatter_parser import FrontmatterParser
        result = FrontmatterParser.parse(content, validate=False)
        return result.raw, result.body

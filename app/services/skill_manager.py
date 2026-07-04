"""Skill 管理模块。

管理 Skill 定义的注册、查询和加载。
Skill 是可复用的指令/能力定义，可以被 Agent 加载。
"""

from __future__ import annotations

from datetime import datetime

from app.models.admin import SkillDefinition
from app.services.config_store import ConfigStore


class SkillManager:
    """Skill 管理器。"""

    _NAMESPACE = "skill"

    def __init__(self, config_store: ConfigStore) -> None:
        self._store = config_store

    async def list_skills(self) -> list[SkillDefinition]:
        """列出所有 Skill。"""
        items = self._store.list(self._NAMESPACE)
        skills: list[SkillDefinition] = []
        for item in items:
            if isinstance(item.value, dict):
                skills.append(SkillDefinition(**item.value))
        return skills

    async def get_skill(self, name: str) -> SkillDefinition | None:
        """获取指定 Skill。"""
        value = self._store.get(self._NAMESPACE, name)
        if value and isinstance(value, dict):
            return SkillDefinition(**value)
        return None

    async def register_skill(self, skill: SkillDefinition) -> None:
        """注册一个 Skill。"""
        skill.updated_at = datetime.now()
        self._store.set(self._NAMESPACE, skill.name, skill.model_dump())

    async def remove_skill(self, name: str) -> None:
        """删除 Skill。"""
        await self._store.delete(self._NAMESPACE, name)

    async def load_from_file(self, path: str) -> SkillDefinition:
        """从文件加载 Skill。

        支持格式:
        - .md 文件 (YAML frontmatter + 指令内容)
        - .json 结构化定义
        """
        import json
        from pathlib import Path

        file_path = Path(path)
        content = file_path.read_text(encoding="utf-8")

        if file_path.suffix == ".json":
            data = json.loads(content)
            skill = SkillDefinition(**data)
        elif file_path.suffix == ".md":
            skill = self._parse_markdown_skill(content, file_path.stem)
        else:
            raise ValueError(f"Unsupported skill file format: {file_path.suffix}")

        skill.source = "file"
        return skill

    def _parse_markdown_skill(self, content: str, default_name: str) -> SkillDefinition:
        """从 Markdown 文件解析 Skill（YAML frontmatter + 正文）。"""
        name = default_name
        description = ""
        instructions = content
        task_types: list[str] = []
        tools: list[str] | None = None

        # 解析 YAML frontmatter (--- ... ---)
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                import yaml
                frontmatter = yaml.safe_load(parts[1]) or {}
                name = frontmatter.get("name", name)
                description = frontmatter.get("description", description)
                task_types = frontmatter.get("task_types", task_types)
                tools = frontmatter.get("tools", tools)
                instructions = parts[2].strip()

        return SkillDefinition(
            name=name,
            description=description,
            instructions=instructions,
            task_types=task_types,
            tools=tools,
            source="file",
        )

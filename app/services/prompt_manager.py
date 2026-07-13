"""提示词管理模块。

管理提示词模板的查询、编辑、恢复默认和按需渲染。
支持内置模板 + 用户自定义模板的双层存储。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import yaml

from app.models.admin import PromptCreateRequest, PromptTemplate
from app.services.sqlite_store import SQLiteStateStore


# 内置默认模板
_BUILTIN_TEMPLATES: dict[str, dict] = {
    "extract_key_points": {
        "name": "extract_key_points",
        "description": "从证据中提取关键发现",
        "template": (
            "基于以下文档上下文和证据，提取关键发现。\n\n"
            "文档: {documents}\n"
            "证据: {evidence}\n"
            "要求: {instructions}\n\n"
            "请输出 JSON 格式的关键发现列表。"
        ),
        "variables": ["documents", "evidence", "instructions"],
        "is_builtin": True,
    },
    "extract_risks": {
        "name": "extract_risks",
        "description": "从证据中提取风险项",
        "template": (
            "基于以下证据，识别潜在风险。\n\n"
            "证据: {evidence}\n"
            "要求: {instructions}\n\n"
            "请输出 JSON 格式的风险列表。"
        ),
        "variables": ["evidence", "instructions"],
        "is_builtin": True,
    },
    "draft_report": {
        "name": "draft_report",
        "description": "生成分析报告草稿",
        "template": (
            "基于以下分析结果，生成一份完整的报告。\n\n"
            "摘要: {summary}\n"
            "关键发现: {key_findings}\n"
            "风险: {risks}\n"
            "证据: {evidence}\n\n"
            "请输出 Markdown 格式的报告。"
        ),
        "variables": ["summary", "key_findings", "risks", "evidence"],
        "is_builtin": True,
    },
    "chat_default": {
        "name": "chat_default",
        "description": "通用对话默认提示词",
        "template": (
            "你是一个有用的 AI 助手。请根据用户的问题提供准确、清晰的回答。\n\n"
            "用户: {message}\n\n"
            "助手:"
        ),
        "variables": ["message"],
        "is_builtin": True,
    },
}


class PromptManager:
    """提示词管理器。

    双层存储：内置模板（硬编码） + 用户自定义（SQLite 持久化）。
    持久化模板覆盖同名的内置模板。
    """

    def __init__(self, persistence: SQLiteStateStore) -> None:
        self._persistence = persistence

    # ── CRUD ──────────────────────────────────

    async def create(self, request: PromptCreateRequest) -> PromptTemplate:
        """创建 Prompt 模板。"""
        now = datetime.now()
        tpl = PromptTemplate(
            id=f"prt-{uuid4().hex[:12]}",
            name=request.name,
            description=request.description,
            template=request.template,
            variables=request.variables,
            is_builtin=False,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._persistence.upsert_prompt(tpl.model_dump(mode="json"))
        return tpl

    async def update(self, prompt_id: str, request: PromptCreateRequest) -> PromptTemplate:
        """更新 Prompt 模板（version 递增）。"""
        existing = await self.get(prompt_id)
        if existing is None:
            raise ValueError(f"Prompt '{prompt_id}' not found")

        now = datetime.now()
        tpl = PromptTemplate(
            id=prompt_id,
            name=request.name,
            description=request.description,
            template=request.template,
            variables=request.variables,
            is_builtin=existing.is_builtin,
            version=existing.version + 1,
            created_at=existing.created_at,
            updated_at=now,
        )
        self._persistence.upsert_prompt(tpl.model_dump(mode="json"))
        return tpl

    async def get(self, prompt_id: str) -> PromptTemplate | None:
        """按 id 获取 Prompt 模板。"""
        payload = self._persistence.get_prompt(prompt_id)
        if payload is None:
            return None
        return PromptTemplate(**payload)

    async def get_by_name(self, name: str) -> PromptTemplate | None:
        """按 name 获取 Prompt 模板。

        优先级：持久化版本 > 内置版本。
        """
        # 先从持久化存储查找
        prompts = await self.list()
        for p in prompts:
            if p.name == name:
                return p
        # 回退到内置模板
        builtin = _BUILTIN_TEMPLATES.get(name)
        if builtin:
            return PromptTemplate(
                id=f"prt-builtin-{name}",
                **builtin,
                version=1,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        return None

    async def list(self) -> list[PromptTemplate]:
        """列出所有 Prompt 模板。

        Returns:
            内置模板 + 持久化模板（持久化覆盖同名内置）。
        """
        # 内置模板
        templates: dict[str, PromptTemplate] = {}
        for name, data in _BUILTIN_TEMPLATES.items():
            templates[name] = PromptTemplate(
                id=f"prt-builtin-{name}",
                version=1,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                **data,
            )

        # 持久化模板覆盖同名内置
        payloads = self._persistence.list_prompts()
        for p in payloads:
            tpl = PromptTemplate(**p)
            templates[tpl.name] = tpl

        return list(templates.values())

    async def delete(self, prompt_id: str) -> None:
        """删除 Prompt 模板（仅删除持久化版本，不影响内置模板）。"""
        self._persistence.delete_prompt(prompt_id)

    async def reset(self, prompt_id: str) -> PromptTemplate | None:
        """恢复 Prompt 模板到内置默认。"""
        existing = await self.get(prompt_id)
        if existing is None:
            return None

        builtin = _BUILTIN_TEMPLATES.get(existing.name)
        if builtin is None:
            # 非内置模板，直接删除
            self._persistence.delete_prompt(prompt_id)
            return None

        now = datetime.now()
        tpl = PromptTemplate(
            id=prompt_id,
            version=existing.version + 1,
            created_at=existing.created_at,
            updated_at=now,
            **builtin,
        )
        self._persistence.upsert_prompt(tpl.model_dump(mode="json"))
        return tpl

    # ── 文件导入 ──────────────────────────────

    async def import_from_file(self, file_path: str) -> PromptTemplate:
        """从 .prompt.md 文件导入 Prompt 模板。"""
        path = Path(file_path)
        if not path.exists():
            raise ValueError(f"File not found: {file_path}")

        frontmatter, body = self._parse_frontmatter(path.read_text(encoding="utf-8"))

        from app.models.admin import PromptCreateRequest

        request = PromptCreateRequest(
            name=frontmatter.get("name", path.stem),
            description=frontmatter.get("description", ""),
            template=body.strip(),
            variables=frontmatter.get("variables", []),
        )
        return await self.create(request)

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """解析 YAML frontmatter。"""
        from app.services.frontmatter_parser import FrontmatterParser
        result = FrontmatterParser.parse(content, validate=False)
        return result.raw, result.body

    # ── 运行时按需渲染 ────────────────────────

    async def render(self, name: str, **variables) -> str:
        """按 name 加载模板并填充变量。

        Prompt 不预加载到系统提示词中，只在具体工作流步骤需要时才调用。

        Args:
            name: 模板名称。
            **variables: 模板变量。

        Returns:
            渲染后的字符串。
        """
        tpl = await self.get_by_name(name)
        if not tpl:
            raise ValueError(f"Prompt '{name}' not found")
        return tpl.template.format(**variables)
"""提示词管理模块。

管理提示词模板的查询、编辑和恢复默认。
"""

from __future__ import annotations

from datetime import datetime

from app.models.admin import PromptTemplate
from app.services.config_store import ConfigStore


# 内置默认模板
_BUILTIN_TEMPLATES: dict[str, PromptTemplate] = {
    "extract_key_points": PromptTemplate(
        name="extract_key_points",
        description="从证据中提取关键发现",
        template=(
            "基于以下文档上下文和证据，提取关键发现。\n\n"
            "文档: {documents}\n"
            "证据: {evidence}\n"
            "要求: {instructions}\n\n"
            "请输出 JSON 格式的关键发现列表。"
        ),
        variables=["documents", "evidence", "instructions"],
        is_builtin=True,
    ),
    "extract_risks": PromptTemplate(
        name="extract_risks",
        description="从证据中提取风险项",
        template=(
            "基于以下证据，识别潜在风险。\n\n"
            "证据: {evidence}\n"
            "要求: {instructions}\n\n"
            "请输出 JSON 格式的风险列表。"
        ),
        variables=["evidence", "instructions"],
        is_builtin=True,
    ),
    "draft_report": PromptTemplate(
        name="draft_report",
        description="生成分析报告草稿",
        template=(
            "基于以下分析结果，生成一份完整的报告。\n\n"
            "摘要: {summary}\n"
            "关键发现: {key_findings}\n"
            "风险: {risks}\n"
            "证据: {evidence}\n\n"
            "请输出 Markdown 格式的报告。"
        ),
        variables=["summary", "key_findings", "risks", "evidence"],
        is_builtin=True,
    ),
    "chat_default": PromptTemplate(
        name="chat_default",
        description="通用对话默认提示词",
        template=(
            "你是一个有用的 AI 助手。请根据用户的问题提供准确、清晰的回答。\n\n"
            "用户: {message}\n\n"
            "助手:"
        ),
        variables=["message"],
        is_builtin=True,
    ),
}


class PromptManager:
    """提示词管理器。"""

    _NAMESPACE = "prompt"

    def __init__(self, config_store: ConfigStore) -> None:
        self._store = config_store

    async def list(self) -> list[PromptTemplate]:
        """列出所有提示词模板。"""
        items = self._store.list(self._NAMESPACE)
        templates: dict[str, PromptTemplate] = dict(_BUILTIN_TEMPLATES)

        # 用持久化的自定义模板覆盖内置模板
        for item in items:
            if isinstance(item.value, dict):
                tpl = PromptTemplate(**item.value)
                templates[tpl.name] = tpl

        return list(templates.values())

    async def get(self, name: str) -> PromptTemplate | None:
        """获取指定模板。"""
        # 先从持久化存储读
        value = self._store.get(self._NAMESPACE, name)
        if value and isinstance(value, dict):
            return PromptTemplate(**value)
        # 回退到内置模板
        return _BUILTIN_TEMPLATES.get(name)

    async def update(self, name: str, template: str) -> PromptTemplate:
        """更新模板内容。"""
        tpl = await self.get(name) or PromptTemplate(name=name)
        tpl.template = template
        tpl.version += 1
        tpl.updated_at = datetime.now()
        self._store.set(self._NAMESPACE, name, tpl.model_dump())
        return tpl

    async def reset(self, name: str) -> PromptTemplate | None:
        """恢复模板到内置默认。"""
        if name in _BUILTIN_TEMPLATES:
            tpl = _BUILTIN_TEMPLATES[name].model_copy()
            tpl.version += 1
            tpl.updated_at = datetime.now()
            self._store.set(
                self._NAMESPACE, name, tpl.model_dump(),
            )
            return tpl
        self._store.delete(self._NAMESPACE, name)
        return _BUILTIN_TEMPLATES.get(name)

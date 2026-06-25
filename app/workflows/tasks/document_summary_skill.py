"""文档摘要领域 skill 模块。

负责把 `document_summary` 任务类型接入统一结构化文档 workflow，使摘要类任务也能复用文档
分析任务图的计划、证据、起草和审查骨架，只在静态规格层体现领域差异。
"""

from __future__ import annotations

from app.workflows.tasks.builtin_skills import get_builtin_task_skill_spec
from app.workflows.tasks.structured_document_skill import StructuredDocumentSkill


class DocumentSummarySkill(StructuredDocumentSkill):
    """把 `document_summary` workflow 表示为第二个内建领域 skill。"""

    def __init__(self) -> None:
        """加载 `document_summary` 的内建规格并初始化基类。"""
        spec = get_builtin_task_skill_spec('document_summary')
        if spec is None:
            raise RuntimeError('missing builtin skill spec: document_summary')
        super().__init__(spec)

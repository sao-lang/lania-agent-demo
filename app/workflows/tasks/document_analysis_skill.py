"""文档分析领域 skill 模块。

负责把 `document_analysis` 任务类型显式注册为一个可调度领域 skill。当前实现本身很薄，
主要作用是把静态 task_type 与通用结构化文档 workflow 样板绑定起来。
"""

from __future__ import annotations

from app.workflows.tasks.builtin_skills import get_builtin_task_skill_spec
from app.workflows.tasks.structured_document_skill import StructuredDocumentSkill


class DocumentAnalysisSkill(StructuredDocumentSkill):
    """把 `document_analysis` workflow 显式表示为领域 skill。"""

    def __init__(self) -> None:
        """加载 `document_analysis` 的内建规格并初始化基类。"""
        spec = get_builtin_task_skill_spec('document_analysis')
        if spec is None:
            raise RuntimeError('missing builtin skill spec: document_analysis')
        super().__init__(spec)

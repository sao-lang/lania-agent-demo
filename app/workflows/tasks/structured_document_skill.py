"""可复用的结构化文档 skill 样板模块。

负责把文档分析类任务共享的 workflow 初始状态构造逻辑沉淀为一个可复用基类。不同领域 skill
只需要注入各自的静态规格，即可复用同一套 document workflow 骨架。
"""

from __future__ import annotations

from typing import Any

from app.models.task import TaskDetail
from app.workflows.tasks.builtin_skills import BuiltinTaskSkillSpec
from app.workflows.tasks.document_analysis_state import init_document_analysis_state


class StructuredDocumentSkill:
    """把同一套 document workflow 包装成可配置领域 skill。"""

    def __init__(self, spec: BuiltinTaskSkillSpec) -> None:
        """从静态规格中提取领域标识和产物元信息。

        Args:
            spec: 内建结构化文档 skill 的静态定义。
        """
        self.task_type = spec.task_type
        self.skill_name = spec.skill_name
        self.display_name = spec.display_name
        self.artifact_type = spec.artifact_type
        self.artifact_title = spec.artifact_title

    def build_initial_state(self, task: TaskDetail) -> dict[str, Any]:
        """构造结构化文档 workflow 的初始状态。

        在通用 `DocumentAnalysisState` 基础上，额外挂入当前领域 skill 的名称、产物类型和产物
        标题，供后续节点在起草、审查和最终提交阶段复用。
        """
        state = init_document_analysis_state(task)
        state['skill_name'] = self.skill_name
        state['artifact_type'] = self.artifact_type
        state['artifact_title'] = self.artifact_title
        return state

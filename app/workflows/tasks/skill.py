"""任务领域 skill 抽象模块。

负责定义 task workflow 可识别的领域 skill 协议，以及 task_type 到具体 skill 实现的注册表。
这样做的目的是把“工作流骨架”与“不同任务类型的初始状态构造逻辑”解耦，方便后续新增更多
任务领域，而不必反复修改编排器主体。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.models.task import TaskDetail


class TaskSkill(Protocol):
    """描述一个可被任务运行时调度的领域 skill。"""

    task_type: str
    skill_name: str

    def build_initial_state(self, task: TaskDetail) -> dict[str, Any]:
        """为当前 skill 构造初始 workflow state。"""


class TaskSkillRegistry:
    """按 `task_type` 管理领域 skill。"""

    def __init__(self) -> None:
        """初始化空的领域 skill 注册表。"""
        self._skills: dict[str, TaskSkill] = {}

    def register(self, skill: TaskSkill) -> None:
        """注册单个 skill，实现 `task_type -> skill` 映射。"""
        self._skills[skill.task_type] = skill

    def register_many(self, skills: list[TaskSkill]) -> None:
        """批量注册多个 skill。"""
        for skill in skills:
            self.register(skill)

    def get(self, task_type: str) -> TaskSkill:
        """按任务类型读取对应 skill；不存在时抛出 `KeyError`。"""
        return self._skills[task_type]

    def has(self, task_type: str) -> bool:
        """判断某个任务类型是否已注册领域 skill。"""
        return task_type in self._skills

    def list(self) -> list[TaskSkill]:
        """按 `task_type` 排序返回当前已注册的全部 skill。"""
        return [self._skills[name] for name in sorted(self._skills)]


def build_default_task_skill_registry() -> TaskSkillRegistry:
    """构建默认 task skill 注册表。

    新增内置 skill 时只需要在这里注册，不需要再改 task runtime 骨架，从而保持编排层对具体
    任务领域的最小感知。
    """
    from app.workflows.tasks.document_analysis_skill import DocumentAnalysisSkill
    from app.workflows.tasks.document_summary_skill import DocumentSummarySkill

    registry = TaskSkillRegistry()
    registry.register_many([DocumentAnalysisSkill(), DocumentSummarySkill()])
    return registry

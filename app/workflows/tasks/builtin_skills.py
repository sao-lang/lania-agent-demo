"""内建任务 skill 静态定义模块。

集中维护 task workflow 内建领域 skill 的静态元信息，例如展示名、产物类型、计划模板类型和
最终输出标签。该模块只保存声明式配置，不承载任何运行时逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinTaskSkillSpec:
    """描述一个内建 task skill 的静态配置。

    这类配置会被 task adapter、领域 skill 和最终产物命名逻辑共同复用，用来保证同一个
    `task_type` 在不同层看到的是一致的元信息。
    """

    task_type: str
    skill_name: str
    display_name: str
    output_label: str
    artifact_type: str
    artifact_title: str
    plan_kind: str = 'structured_document'


_BUILTIN_TASK_SKILL_SPECS: dict[str, BuiltinTaskSkillSpec] = {
    'document_analysis': BuiltinTaskSkillSpec(
        task_type='document_analysis',
        skill_name='document_analysis',
        display_name='文档分析',
        output_label='分析报告',
        artifact_type='document_analysis_report',
        artifact_title='文档分析报告',
    ),
    'document_summary': BuiltinTaskSkillSpec(
        task_type='document_summary',
        skill_name='document_summary',
        display_name='文档摘要',
        output_label='摘要报告',
        artifact_type='document_summary_report',
        artifact_title='文档摘要报告',
    ),
    'chat': BuiltinTaskSkillSpec(
        task_type='chat',
        skill_name='chat',
        display_name='通用对话',
        output_label='对话结果',
        artifact_type='chat_result',
        artifact_title='对话记录',
        plan_kind='chat',
    ),
}


def get_builtin_task_skill_spec(task_type: str) -> BuiltinTaskSkillSpec | None:
    """按 `task_type` 返回内建 skill 定义。"""
    return _BUILTIN_TASK_SKILL_SPECS.get(task_type)


def list_builtin_task_skill_specs() -> list[BuiltinTaskSkillSpec]:
    """返回全部内建 skill 定义，结果按 `task_type` 排序。"""
    return [_BUILTIN_TASK_SKILL_SPECS[name] for name in sorted(_BUILTIN_TASK_SKILL_SPECS)]

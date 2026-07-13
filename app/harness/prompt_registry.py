"""Prompt 版本注册表与回归评估模块。

把 PromptBuilder 的模板管理与架构文档定义的 PromptSpec / PromptProfile /
PromptEvaluationCase 契约对接，建立版本化、可回归的 prompt 治理面。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class PromptEvaluationCase(BaseModel):
    """一条 prompt 回归评估样例。"""

    case_id: str
    input_fixture: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: str = ''
    expected_tool_usage: list[str] = Field(default_factory=list)
    expected_output_schema: str | None = None
    assertions: list[str] = Field(default_factory=list)


class PromptRegressionReport(BaseModel):
    """一次 prompt 版本评估的报告。"""

    report_id: str
    prompt_spec_ref: str
    baseline_version: str
    candidate_version: str
    pass_rate: float = 0.0
    tested_cases: int = 0
    passed_cases: int = 0
    regressions: list[str] = Field(default_factory=list)
    tool_misuse_rate: float = 0.0
    schema_violation_rate: float = 0.0
    notes: list[str] = Field(default_factory=list)
    created_at: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat()


class PromptProfile(BaseModel):
    """描述某类任务或某用户默认采用的 prompt 偏好与控制。"""

    profile_id: str
    scope: Literal['platform', 'tenant', 'user', 'skill'] = 'platform'
    default_language: str = 'zh-CN'
    verbosity_level: Literal['low', 'medium', 'high'] = 'medium'
    reasoning_style: Literal['compact', 'structured', 'deliberate'] = 'structured'
    format_preferences: dict[str, Any] = Field(default_factory=dict)
    safety_mode: Literal['strict', 'balanced', 'open'] = 'balanced'
    enabled_prompt_packs: list[str] = Field(default_factory=list)


class PromptVersion(BaseModel):
    """prompt 模板的版本化记录。"""

    prompt_id: str
    version: str
    content: str
    output_schema: str = ''
    change_log: list[str] = Field(default_factory=list)
    created_at: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat()


class PromptVersionRegistry:
    """prompt 模板版本注册表，支持版本管理、回滚和评估。"""

    def __init__(self) -> None:
        self._versions: dict[str, list[PromptVersion]] = {}
        self._active_versions: dict[str, str] = {}
        self._evaluation_cases: dict[str, list[PromptEvaluationCase]] = {}
        self._profiles: dict[str, PromptProfile] = {}

    def register_version(
        self,
        prompt_id: str,
        content: str,
        output_schema: str = '',
        change_log: list[str] | None = None,
        *,
        activate: bool = True,
    ) -> PromptVersion:
        """注册一个新版本。

        Args:
            prompt_id: 模板 ID（如 'extract_key_points'）。
            content: prompt 内容。
            output_schema: 期望输出 schema。
            change_log: 变更说明。
            activate: 是否同时将本版本设为当前活跃版本。

        Returns:
            创建的 PromptVersion 实例。
        """
        existing = self._versions.get(prompt_id, [])
        version_str = f'v{len(existing) + 1}'

        pv = PromptVersion(
            prompt_id=prompt_id,
            version=version_str,
            content=content,
            output_schema=output_schema,
            change_log=change_log or [],
        )
        self._versions.setdefault(prompt_id, []).append(pv)

        if activate:
            self._active_versions[prompt_id] = version_str

        return pv

    def get_active_version(self, prompt_id: str) -> PromptVersion | None:
        """获取当前活跃版本。"""
        version_str = self._active_versions.get(prompt_id)
        if version_str is None:
            return None
        for v in self._versions.get(prompt_id, []):
            if v.version == version_str:
                return v
        return None

    def get_version(
        self, prompt_id: str, version: str
    ) -> PromptVersion | None:
        """获取指定版本。"""
        for v in self._versions.get(prompt_id, []):
            if v.version == version:
                return v
        return None

    def list_versions(self, prompt_id: str) -> list[PromptVersion]:
        """列出某模板的所有版本。"""
        return list(self._versions.get(prompt_id, []))

    def rollback_to(
        self, prompt_id: str, version: str
    ) -> PromptVersion | None:
        """回滚到指定版本。"""
        target = self.get_version(prompt_id, version)
        if target is None:
            return None
        self._active_versions[prompt_id] = version
        return target

    def list_all_prompt_ids(self) -> list[str]:
        """列出所有已注册的模板 ID。"""
        return sorted(self._versions.keys())

    def register_profile(self, profile: PromptProfile) -> None:
        """注册一个 prompt profile。"""
        self._profiles[profile.profile_id] = profile

    def get_profile(self, profile_id: str) -> PromptProfile | None:
        """获取 prompt profile。"""
        return self._profiles.get(profile_id)

    def register_evaluation_case(
        self, prompt_id: str, case: PromptEvaluationCase
    ) -> None:
        """为某模板注册一条评估样例。"""
        self._evaluation_cases.setdefault(prompt_id, []).append(case)

    def get_evaluation_cases(
        self, prompt_id: str
    ) -> list[PromptEvaluationCase]:
        """获取某模板的评估样例列表。"""
        return list(self._evaluation_cases.get(prompt_id, []))

    def generate_report(
        self,
        prompt_id: str,
        baseline_version: str,
        candidate_version: str,
    ) -> PromptRegressionReport:
        """生成两个版本之间的回归测试报告。

        当前为占位实现，需要对接实际 LLM 调用来做真实验证。
        """
        cases = self.get_evaluation_cases(prompt_id)
        return PromptRegressionReport(
            report_id=f'report-{uuid4().hex[:12]}',
            prompt_spec_ref=prompt_id,
            baseline_version=baseline_version,
            candidate_version=candidate_version,
            tested_cases=len(cases),
            passed_cases=0,
            pass_rate=0.0,
            regressions=[],
            notes=[
                '此报告是占位结果；',
                '需要对接评估流水线以获取真实回归数据。',
            ],
        )

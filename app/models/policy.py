"""策略画像模型模块。

负责定义 `Policy Harness` 的数据库化配置对象，以及列表、创建、更新等接口使用的
数据模型，供任务服务和 API 统一复用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PolicyProfileItem(BaseModel):
    """可持久化的策略画像定义。

    该模型把权限、输出格式、证据要求和基线选择策略收敛成一个可复用的策略配置对象。
    """

    profile_id: str
    name: str = Field(min_length=1)
    version: str = 'v1'
    is_default: bool = False
    organization_id: str | None = None
    tenant_id: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    match_keywords: list[str] = Field(default_factory=list)
    require_evidence: bool = True
    min_coverage: float = 0.0
    confidence_threshold: float = 0.0
    require_review_passed: bool = False
    allowed_output_formats: list[Literal['markdown', 'json', 'markdown+json']] = Field(
        default_factory=lambda: ['markdown', 'json', 'markdown+json']
    )
    blocked_tools: list[str] = Field(default_factory=list)
    denied_permissions: list[str] = Field(default_factory=list)
    evaluation_baseline_order: list[Literal['benchmark', 'report', 'version', 'task']] = Field(
        default_factory=lambda: ['benchmark', 'report', 'version', 'task']
    )
    evaluation_report_path: str | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class PolicyProfileListResponse(BaseModel):
    """策略画像分页列表。

    对列表结果和筛选上下文做统一封装，便于 API 稳定返回。
    """

    total: int = 0
    limit: int = 20
    offset: int = 0
    organization_id: str | None = None
    tenant_id: str | None = None
    items: list[PolicyProfileItem] = Field(default_factory=list)


class PolicyProfileCreateRequest(BaseModel):
    """创建策略画像请求。

    字段基本与 `PolicyProfileItem` 对齐，但不包含持久化生成的标识和时间戳。
    """

    name: str = Field(min_length=1)
    version: str = 'v1'
    is_default: bool = False
    organization_id: str | None = None
    tenant_id: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    match_keywords: list[str] = Field(default_factory=list)
    require_evidence: bool = True
    min_coverage: float = 0.0
    confidence_threshold: float = 0.0
    require_review_passed: bool = False
    allowed_output_formats: list[Literal['markdown', 'json', 'markdown+json']] = Field(
        default_factory=lambda: ['markdown', 'json', 'markdown+json']
    )
    blocked_tools: list[str] = Field(default_factory=list)
    denied_permissions: list[str] = Field(default_factory=list)
    evaluation_baseline_order: list[Literal['benchmark', 'report', 'version', 'task']] = Field(
        default_factory=lambda: ['benchmark', 'report', 'version', 'task']
    )
    evaluation_report_path: str | None = None
    description: str | None = None


class PolicyProfileUpdateRequest(BaseModel):
    """更新策略画像请求。

    使用全可选字段表达局部更新语义，避免调用方每次更新都回传整份策略画像。
    """

    name: str | None = None
    version: str | None = None
    is_default: bool | None = None
    organization_id: str | None = None
    tenant_id: str | None = None
    allowed_roles: list[str] | None = None
    match_keywords: list[str] | None = None
    require_evidence: bool | None = None
    min_coverage: float | None = None
    confidence_threshold: float | None = None
    require_review_passed: bool | None = None
    allowed_output_formats: list[Literal['markdown', 'json', 'markdown+json']] | None = None
    blocked_tools: list[str] | None = None
    denied_permissions: list[str] | None = None
    evaluation_baseline_order: list[Literal['benchmark', 'report', 'version', 'task']] | None = None
    evaluation_report_path: str | None = None
    description: str | None = None

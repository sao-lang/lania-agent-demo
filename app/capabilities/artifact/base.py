"""Artifact 能力契约模块。

定义 artifact 列表查询与单项读取所需的数据模型和稳定接口，为运行时状态、
持久化适配以及上层服务提供一致的访问边界。
"""


from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.models.artifact import Artifact


class ArtifactListRequest(BaseModel):
    """列出 artifact 请求。"""

    task_id: str | None = None
    artifact_type: str | None = None
    status: Literal['draft', 'final'] | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ArtifactSummaryItem(BaseModel):
    """artifact 摘要。"""

    artifact_id: str
    task_id: str
    artifact_type: str
    version: int = Field(default=1, ge=1)
    status: Literal['draft', 'final'] = 'draft'
    title: str | None = None
    summary: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime
    updated_at: datetime


class ArtifactListResult(BaseModel):
    """artifact 列表结果。"""

    items: list[ArtifactSummaryItem] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1)
    offset: int = Field(default=0, ge=0)


class ArtifactCapability(Protocol):
    """稳定的 artifact 能力接口。"""

    def list_artifacts(self, request: ArtifactListRequest) -> ArtifactListResult:
        """列出 artifacts。"""

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """读取单个 artifact。"""

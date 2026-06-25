"""本地 Artifact 能力实现模块。

基于内存状态与 SQLite 持久化结果提供 artifact 列表和详情读取能力，统一处理
运行时缓存回填、筛选分页以及模型转换。
"""


from __future__ import annotations

from typing import cast

from app.capabilities.artifact.base import ArtifactCapability, ArtifactListRequest, ArtifactListResult, ArtifactSummaryItem
from app.models.artifact import Artifact
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import ArtifactRecord


class LocalArtifactCapability(ArtifactCapability):
    """基于运行时 state/persistence 的 artifact 能力。"""

    def __init__(self, state: InMemoryState, persistence: SQLiteStateStore | None = None) -> None:
        """初始化 artifact 能力所需的内存状态与持久化依赖。"""
        self.state = state
        self.persistence = persistence

    def list_artifacts(self, request: ArtifactListRequest) -> ArtifactListResult:
        """按任务、类型和状态筛选 artifact，并返回分页摘要。"""
        artifacts = self._load_artifacts(task_id=request.task_id)
        if request.artifact_type is not None:
            artifacts = [item for item in artifacts if item.artifact_type == request.artifact_type]
        if request.status is not None:
            artifacts = [item for item in artifacts if item.status == request.status]
        ordered = sorted(artifacts, key=lambda item: (item.updated_at, item.created_at, item.version), reverse=True)
        page = ordered[request.offset : request.offset + request.limit]
        return ArtifactListResult(
            items=[
                ArtifactSummaryItem(
                    artifact_id=item.artifact_id,
                    task_id=item.task_id,
                    artifact_type=item.artifact_type,
                    version=item.version,
                    status=item.status,
                    title=item.content.title,
                    summary=item.content.summary,
                    confidence=item.content.confidence,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                for item in page
            ],
            total=len(ordered),
            limit=request.limit,
            offset=request.offset,
        )

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """优先从持久化层回填后读取单个 artifact。"""
        if self.persistence is not None:
            payload = self.persistence.get_artifact(artifact_id)
            if payload is not None:
                self.state.artifacts[artifact_id] = cast(ArtifactRecord, payload)
                return Artifact.model_validate(payload)
        payload = self.state.artifacts.get(artifact_id)
        if payload is None:
            return None
        return Artifact.model_validate(payload)

    def _load_artifacts(self, task_id: str | None = None) -> list[Artifact]:
        """从持久化层或内存状态加载 artifact 列表。"""
        if self.persistence is not None:
            persisted = (
                self.persistence.list_artifacts_for_task(task_id)
                if task_id is not None
                else self.persistence.list_artifacts()
            )
            if persisted:
                self.state.artifacts.update({item['artifact_id']: cast(ArtifactRecord, item) for item in persisted})
                return [Artifact.model_validate(item) for item in persisted]
        items = self.state.artifacts.values()
        if task_id is not None:
            items = [item for item in items if item.get('task_id') == task_id]
        return [Artifact.model_validate(item) for item in items]

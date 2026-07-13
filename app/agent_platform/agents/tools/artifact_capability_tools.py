"""产物能力工具模块�?
对外提供产物列表查询与单产物读取能力，供任务链路在不直接操作底层 artifact
service 的情况下访问历史产物与当前任务结果�?"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent_platform.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.agent_platform.capabilities.artifact import ArtifactListRequest, ArtifactListResult
from app.models.artifact import Artifact


class ListArtifactsInput(BaseModel):
    """列出 artifact 输入�?""

    task_id: str | None = None
    artifact_type: str | None = None
    status: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ReadArtifactInput(BaseModel):
    """读取 artifact 输入�?""

    artifact_id: str = Field(min_length=1)


class ListArtifactsTool:
    """列出 artifacts�?""

    name = 'list_artifacts'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ListArtifactsInput
    output_model = ArtifactListResult

    def run(self, payload: ListArtifactsInput, context) -> ArtifactListResult:
        """按任务、类型或状态过滤产物列表�?""

        if context.artifact is None:
            raise ToolExecutionError(
                code='artifact_capability_unavailable',
                message='artifact capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.artifact.list_artifacts(
            ArtifactListRequest(
                task_id=payload.task_id,
                artifact_type=payload.artifact_type,
                status=payload.status,
                limit=payload.limit,
                offset=payload.offset,
            )
        )


class ReadArtifactTool:
    """读取单个 artifact�?""

    name = 'read_artifact'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ReadArtifactInput
    output_model = Artifact

    def run(self, payload: ReadArtifactInput, context) -> Artifact:
        """读取单个产物详情，不存在时抛出统一工具错误�?""

        if context.artifact is None:
            raise ToolExecutionError(
                code='artifact_capability_unavailable',
                message='artifact capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        result = context.artifact.get_artifact(payload.artifact_id)
        if result is None:
            raise ToolExecutionError(
                code='artifact_not_found',
                message=f'artifact not found: {payload.artifact_id}',
                error_type='not_found',
                default_action='abort',
            )
        return result

"""仓库能力工具模块。

封装代码仓检索、文件读取和目录枚举三类基础能力，供受控子代理与任务步骤在
不直接接触底层 repository capability 的前提下访问仓库内容。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.capabilities.repository import (
    RepositoryListFilesRequest,
    RepositoryListFilesResult,
    RepositoryReadFileRequest,
    RepositoryReadFileResult,
    RepositorySearchRequest,
    RepositorySearchResult,
)


class SearchRepositoryInput(BaseModel):
    """仓库搜索输入。"""

    query: str = Field(min_length=1)
    path_prefix: str = '.'
    max_results: int = Field(default=20, ge=1, le=200)


class ReadRepositoryFileInput(BaseModel):
    """读取仓库文件输入。"""

    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=80, ge=1, le=500)


class ListRepositoryFilesInput(BaseModel):
    """列出仓库文件输入。"""

    path_prefix: str = '.'
    recursive: bool = True
    max_entries: int = Field(default=100, ge=1, le=1000)


class SearchRepositoryTool:
    """按文本搜索仓库内容。"""

    name = 'search_repository'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = SearchRepositoryInput
    output_model = RepositorySearchResult

    def run(self, payload: SearchRepositoryInput, context) -> RepositorySearchResult:
        """执行仓库文本搜索。"""

        if context.repository is None:
            raise ToolExecutionError(
                code='repository_capability_unavailable',
                message='repository capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.repository.search_text(
            RepositorySearchRequest(
                query=payload.query,
                path_prefix=payload.path_prefix,
                max_results=payload.max_results,
            )
        )


class ReadRepositoryFileTool:
    """读取仓库文件片段。"""

    name = 'read_repository_file'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ReadRepositoryFileInput
    output_model = RepositoryReadFileResult

    def run(self, payload: ReadRepositoryFileInput, context) -> RepositoryReadFileResult:
        """读取指定文件的连续片段。"""

        if context.repository is None:
            raise ToolExecutionError(
                code='repository_capability_unavailable',
                message='repository capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.repository.read_file(
            RepositoryReadFileRequest(
                path=payload.path,
                start_line=payload.start_line,
                max_lines=payload.max_lines,
            )
        )


class ListRepositoryFilesTool:
    """列出仓库文件。"""

    name = 'list_repository_files'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ListRepositoryFilesInput
    output_model = RepositoryListFilesResult

    def run(self, payload: ListRepositoryFilesInput, context) -> RepositoryListFilesResult:
        """列出指定路径前缀下的仓库文件。"""

        if context.repository is None:
            raise ToolExecutionError(
                code='repository_capability_unavailable',
                message='repository capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.repository.list_files(
            RepositoryListFilesRequest(
                path_prefix=payload.path_prefix,
                recursive=payload.recursive,
                max_entries=payload.max_entries,
            )
        )

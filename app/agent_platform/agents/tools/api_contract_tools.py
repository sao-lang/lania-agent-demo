"""API Contract 能力工具模块�?
对外暴露列举 contract、搜索接口操作和读取接口定义三类工具，供任务工作流在
分析仓库接口契约时通过统一工具协议访问底层 capability�?"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent_platform.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.agent_platform.capabilities.api_contract import (
    ApiContractListRequest,
    ApiContractListResult,
    ApiContractReadRequest,
    ApiContractReadResult,
    ApiContractSearchOperationsRequest,
    ApiContractSearchOperationsResult,
)


class ListApiContractsInput(BaseModel):
    """列出 API contract 输入�?""

    path_prefix: str = '.'
    max_entries: int = Field(default=50, ge=1, le=500)


class SearchApiContractOperationsInput(BaseModel):
    """搜索 API contract operations 输入�?""

    query: str = Field(min_length=1)
    path_prefix: str = '.'
    max_results: int = Field(default=20, ge=1, le=200)


class ReadApiContractInput(BaseModel):
    """读取 API contract 输入�?""

    path: str = Field(min_length=1)
    method: str | None = None
    endpoint_path: str | None = None


class ListApiContractsTool:
    """列出 API contract 文档�?""

    name = 'list_api_contracts'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ListApiContractsInput
    output_model = ApiContractListResult

    def run(self, payload: ListApiContractsInput, context) -> ApiContractListResult:
        """调用 API contract capability 列出可用契约文档�?""

        if context.api_contract is None:
            raise ToolExecutionError(
                code='api_contract_capability_unavailable',
                message='api contract capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.api_contract.list_contracts(
            ApiContractListRequest(path_prefix=payload.path_prefix, max_entries=payload.max_entries)
        )


class SearchApiContractOperationsTool:
    """搜索 API contract operations�?""

    name = 'search_api_contract_operations'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = SearchApiContractOperationsInput
    output_model = ApiContractSearchOperationsResult

    def run(self, payload: SearchApiContractOperationsInput, context) -> ApiContractSearchOperationsResult:
        """按关键词搜索契约中的接口操作�?""

        if context.api_contract is None:
            raise ToolExecutionError(
                code='api_contract_capability_unavailable',
                message='api contract capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.api_contract.search_operations(
            ApiContractSearchOperationsRequest(
                query=payload.query,
                path_prefix=payload.path_prefix,
                max_results=payload.max_results,
            )
        )


class ReadApiContractTool:
    """读取 API contract 结构化内容�?""

    name = 'read_api_contract'
    version = 'v1'
    timeout_ms = 5000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = ReadApiContractInput
    output_model = ApiContractReadResult

    def run(self, payload: ReadApiContractInput, context) -> ApiContractReadResult:
        """读取指定接口契约的结构化定义内容�?""

        if context.api_contract is None:
            raise ToolExecutionError(
                code='api_contract_capability_unavailable',
                message='api contract capability is not configured',
                error_type='dependency_error',
                default_action='fallback',
            )
        return context.api_contract.read_contract(
            ApiContractReadRequest(
                path=payload.path,
                method=payload.method,
                endpoint_path=payload.endpoint_path,
            )
        )

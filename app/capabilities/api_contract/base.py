"""API Contract 能力契约模块。

定义 API 契约检索、操作搜索与结构化读取所需的请求/响应模型以及稳定协议，
为本地实现和后续远程 provider 提供一致的输入输出边界。
"""


from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class ApiContractListRequest(BaseModel):
    """列出 API contract 文档请求。"""

    path_prefix: str = '.'
    max_entries: int = Field(default=50, ge=1, le=500)


class ApiContractDocument(BaseModel):
    """API contract 文档摘要。"""

    path: str
    format: str
    title: str | None = None
    version: str | None = None
    operation_count: int = Field(default=0, ge=0)


class ApiContractListResult(BaseModel):
    """API contract 文档列表。"""

    root_path: str
    contracts: list[ApiContractDocument] = Field(default_factory=list)
    truncated: bool = False


class ApiContractSearchOperationsRequest(BaseModel):
    """搜索 API contract operation 请求。"""

    query: str = Field(min_length=1)
    path_prefix: str = '.'
    max_results: int = Field(default=20, ge=1, le=200)

    @field_validator('query', 'path_prefix')
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        """去除查询与路径前缀两端空白，并拒绝空字符串输入。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned


class ApiContractOperationMatch(BaseModel):
    """API contract operation 命中项。"""

    contract_path: str
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)


class ApiContractSearchOperationsResult(BaseModel):
    """API contract operation 搜索结果。"""

    root_path: str
    query: str
    matches: list[ApiContractOperationMatch] = Field(default_factory=list)
    truncated: bool = False


class ApiContractReadRequest(BaseModel):
    """读取 API contract 请求。"""

    path: str = Field(min_length=1)
    method: str | None = None
    endpoint_path: str | None = None

    @field_validator('path')
    @classmethod
    def _strip_path(cls, value: str) -> str:
        """规范化契约路径参数，确保后续文件定位基于非空路径。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned

    @field_validator('method')
    @classmethod
    def _normalize_method(cls, value: str | None) -> str | None:
        """把请求中的 HTTP 方法规范化为小写，便于与契约定义精确匹配。"""
        if value is None:
            return None
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator('endpoint_path')
    @classmethod
    def _normalize_endpoint_path(cls, value: str | None) -> str | None:
        """清理接口路径筛选条件，允许空白值退化为未指定。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ApiContractOperation(BaseModel):
    """API contract 中的单个 operation。"""

    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class ApiContractReadResult(BaseModel):
    """读取 API contract 的结构化结果。"""

    root_path: str
    path: str
    format: str
    title: str | None = None
    version: str | None = None
    servers: list[str] = Field(default_factory=list)
    operations: list[ApiContractOperation] = Field(default_factory=list)
    selected_operation: ApiContractOperation | None = None


class ApiContractCapability(Protocol):
    """稳定的 API contract 能力接口。"""

    root_path: Path

    def list_contracts(self, request: ApiContractListRequest) -> ApiContractListResult:
        """列出 API contract 文档。"""

    def search_operations(self, request: ApiContractSearchOperationsRequest) -> ApiContractSearchOperationsResult:
        """搜索 API contract operations。"""

    def read_contract(self, request: ApiContractReadRequest) -> ApiContractReadResult:
        """读取 API contract 结构化内容。"""

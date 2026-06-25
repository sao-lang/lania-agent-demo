"""仓库能力契约模块。

定义仓库目录遍历、文本搜索和文件片段读取所需的请求/响应模型与稳定协议，
为本地文件系统实现与上层工具调用提供统一边界。
"""


from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class RepositoryListFilesRequest(BaseModel):
    """列出仓库文件请求。"""

    path_prefix: str = '.'
    recursive: bool = True
    max_entries: int = Field(default=100, ge=1, le=1000)


class RepositoryFileEntry(BaseModel):
    """仓库文件条目。"""

    path: str
    is_dir: bool = False
    size_bytes: int = 0


class RepositoryListFilesResult(BaseModel):
    """列出仓库文件结果。"""

    root_path: str
    entries: list[RepositoryFileEntry] = Field(default_factory=list)
    truncated: bool = False


class RepositorySearchRequest(BaseModel):
    """仓库文本搜索请求。"""

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


class RepositorySearchMatch(BaseModel):
    """仓库搜索命中。"""

    path: str
    line_number: int = Field(ge=1)
    line_text: str


class RepositorySearchResult(BaseModel):
    """仓库文本搜索结果。"""

    root_path: str
    query: str
    matches: list[RepositorySearchMatch] = Field(default_factory=list)
    truncated: bool = False


class RepositoryReadFileRequest(BaseModel):
    """读取仓库文件请求。"""

    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=80, ge=1, le=500)

    @field_validator('path')
    @classmethod
    def _strip_path(cls, value: str) -> str:
        """规范化文件路径参数，确保后续读取基于非空路径。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('must not be empty')
        return cleaned


class RepositoryReadFileResult(BaseModel):
    """读取仓库文件结果。"""

    root_path: str
    path: str
    content: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=0)
    truncated: bool = False


class RepositoryCapability(Protocol):
    """描述一个稳定的仓库能力接口。"""

    root_path: Path

    def list_files(self, request: RepositoryListFilesRequest) -> RepositoryListFilesResult:
        """列出仓库中文件。"""

    def search_text(self, request: RepositorySearchRequest) -> RepositorySearchResult:
        """按文本搜索仓库内容。"""

    def read_file(self, request: RepositoryReadFileRequest) -> RepositoryReadFileResult:
        """读取指定仓库文件片段。"""

"""仓库能力导出模块。

统一导出本地仓库浏览、全文搜索与文件读取所需的契约与实现，供 agent、工具与
workflow 在受控边界内访问代码仓库内容。
"""


from app.capabilities.repository.base import (
    RepositoryCapability,
    RepositoryFileEntry,
    RepositoryListFilesRequest,
    RepositoryListFilesResult,
    RepositoryReadFileRequest,
    RepositoryReadFileResult,
    RepositorySearchMatch,
    RepositorySearchRequest,
    RepositorySearchResult,
)
from app.capabilities.repository.service import LocalRepositoryCapability, build_repository_capability

__all__ = [
    'LocalRepositoryCapability',
    'RepositoryCapability',
    'RepositoryFileEntry',
    'RepositoryListFilesRequest',
    'RepositoryListFilesResult',
    'RepositoryReadFileRequest',
    'RepositoryReadFileResult',
    'RepositorySearchMatch',
    'RepositorySearchRequest',
    'RepositorySearchResult',
    'build_repository_capability',
]

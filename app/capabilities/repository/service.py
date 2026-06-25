"""本地仓库能力实现模块。

基于本地文件系统提供目录列举、关键字搜索和文件片段读取能力，并统一处理
路径越界保护与文本文件读取的边界情况。
"""


from __future__ import annotations

from pathlib import Path

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


class LocalRepositoryCapability(RepositoryCapability):
    """基于本地文件系统提供仓库查询能力。"""

    def __init__(self, root_path: Path) -> None:
        """初始化本地仓库能力的根目录。"""
        self.root_path = root_path.resolve()

    def list_files(self, request: RepositoryListFilesRequest) -> RepositoryListFilesResult:
        """列出目录下的文件条目，并限制结果数量避免过量扫描。"""
        base = self._resolve_relative_path(request.path_prefix)
        entries: list[RepositoryFileEntry] = []
        truncated = False
        iterator = base.rglob('*') if request.recursive else base.iterdir()
        for item in iterator:
            if item.name.startswith('.git'):
                continue
            entries.append(
                RepositoryFileEntry(
                    path=str(item.relative_to(self.root_path)),
                    is_dir=item.is_dir(),
                    size_bytes=item.stat().st_size if item.is_file() else 0,
                )
            )
            if len(entries) >= request.max_entries:
                truncated = True
                break
        return RepositoryListFilesResult(root_path=str(self.root_path), entries=entries, truncated=truncated)

    def search_text(self, request: RepositorySearchRequest) -> RepositorySearchResult:
        """在仓库文本文件中搜索关键字并返回命中行。"""
        base = self._resolve_relative_path(request.path_prefix)
        matches: list[RepositorySearchMatch] = []
        truncated = False
        query = request.query.lower()
        for item in base.rglob('*'):
            if not item.is_file() or item.name.startswith('.git'):
                continue
            try:
                lines = item.read_text(encoding='utf-8').splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for index, line in enumerate(lines, start=1):
                if query not in line.lower():
                    continue
                matches.append(
                    RepositorySearchMatch(
                        path=str(item.relative_to(self.root_path)),
                        line_number=index,
                        line_text=line[:500],
                    )
                )
                if len(matches) >= request.max_results:
                    truncated = True
                    return RepositorySearchResult(
                        root_path=str(self.root_path),
                        query=request.query,
                        matches=matches,
                        truncated=truncated,
                    )
        return RepositorySearchResult(root_path=str(self.root_path), query=request.query, matches=matches, truncated=truncated)

    def read_file(self, request: RepositoryReadFileRequest) -> RepositoryReadFileResult:
        """读取指定文件的受控行区间内容。"""
        path = self._resolve_relative_path(request.path)
        if not path.is_file():
            raise FileNotFoundError(request.path)
        lines = path.read_text(encoding='utf-8').splitlines()
        start_index = request.start_line - 1
        selected = lines[start_index : start_index + request.max_lines]
        end_line = start_index + len(selected)
        truncated = end_line < len(lines)
        return RepositoryReadFileResult(
            root_path=str(self.root_path),
            path=str(path.relative_to(self.root_path)),
            content='\n'.join(selected),
            start_line=request.start_line,
            end_line=end_line,
            truncated=truncated,
        )

    def _resolve_relative_path(self, raw_path: str) -> Path:
        """把相对路径解析到仓库根目录内，并阻止越界访问。"""
        relative = Path(raw_path)
        if relative.is_absolute():
            candidate = relative.resolve()
        else:
            candidate = (self.root_path / relative).resolve()
        if candidate != self.root_path and self.root_path not in candidate.parents:
            raise PermissionError(f'path escapes repository root: {raw_path}')
        if not candidate.exists():
            raise FileNotFoundError(raw_path)
        return candidate


def build_repository_capability(root_path: Path | None = None) -> RepositoryCapability:
    """构建默认本地仓库能力。"""
    return LocalRepositoryCapability((root_path or Path.cwd()).resolve())

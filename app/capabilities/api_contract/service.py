"""本地 API Contract 能力实现模块。

基于仓库文件系统扫描 OpenAPI/Swagger 文档，并把契约文件解析、筛选、读取与
operation 提取等行为封装成稳定的 capability 服务。
"""


from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.capabilities.api_contract.base import (
    ApiContractCapability,
    ApiContractDocument,
    ApiContractListRequest,
    ApiContractListResult,
    ApiContractOperation,
    ApiContractOperationMatch,
    ApiContractReadRequest,
    ApiContractReadResult,
    ApiContractSearchOperationsRequest,
    ApiContractSearchOperationsResult,
)


class LocalApiContractCapability(ApiContractCapability):
    """基于本地仓库文件系统的 API contract 能力。"""

    SUPPORTED_SUFFIXES = {'.json', '.yaml', '.yml'}
    SUPPORTED_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'}

    def __init__(self, root_path: Path) -> None:
        """初始化本地 API 契约能力的仓库根目录。"""
        self.root_path = root_path.resolve()

    def list_contracts(self, request: ApiContractListRequest) -> ApiContractListResult:
        """扫描目录下的契约文件并返回结构化摘要列表。"""
        base = self._resolve_relative_path(request.path_prefix)
        contracts: list[ApiContractDocument] = []
        truncated = False
        for item in sorted(base.rglob('*')):
            if not item.is_file() or item.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                continue
            spec = self._load_contract_spec(item)
            if spec is None:
                continue
            contracts.append(
                ApiContractDocument(
                    path=str(item.relative_to(self.root_path)),
                    format=self._detect_format(item, spec),
                    title=self._extract_title(spec),
                    version=self._extract_version(spec),
                    operation_count=len(self._extract_operations(spec)),
                )
            )
            if len(contracts) >= request.max_entries:
                truncated = True
                break
        return ApiContractListResult(root_path=str(self.root_path), contracts=contracts, truncated=truncated)

    def search_operations(self, request: ApiContractSearchOperationsRequest) -> ApiContractSearchOperationsResult:
        """搜索契约中与查询词匹配的接口操作。"""
        base = self._resolve_relative_path(request.path_prefix)
        query = request.query.lower()
        matches: list[ApiContractOperationMatch] = []
        for item in sorted(base.rglob('*')):
            if not item.is_file() or item.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                continue
            spec = self._load_contract_spec(item)
            if spec is None:
                continue
            for operation in self._extract_operations(spec):
                search_blob = ' '.join(
                    [
                        operation.method,
                        operation.path,
                        operation.operation_id or '',
                        operation.summary or '',
                        operation.description or '',
                        ' '.join(operation.tags),
                    ]
                ).lower()
                if query not in search_blob:
                    continue
                matches.append(
                    ApiContractOperationMatch(
                        contract_path=str(item.relative_to(self.root_path)),
                        method=operation.method,
                        path=operation.path,
                        operation_id=operation.operation_id,
                        summary=operation.summary,
                        tags=list(operation.tags),
                    )
                )
                if len(matches) >= request.max_results:
                    return ApiContractSearchOperationsResult(
                        root_path=str(self.root_path),
                        query=request.query,
                        matches=matches,
                        truncated=True,
                    )
        return ApiContractSearchOperationsResult(
            root_path=str(self.root_path),
            query=request.query,
            matches=matches,
            truncated=False,
        )

    def read_contract(self, request: ApiContractReadRequest) -> ApiContractReadResult:
        """读取单个契约文档，并在需要时定位指定操作。"""
        path = self._resolve_relative_path(request.path)
        if not path.is_file():
            raise FileNotFoundError(request.path)
        spec = self._load_contract_spec(path)
        if spec is None:
            raise ValueError(f'not a valid api contract: {request.path}')
        operations = self._extract_operations(spec)
        selected_operation = None
        if request.method is not None and request.endpoint_path is not None:
            selected_operation = next(
                (
                    item
                    for item in operations
                    if item.method == request.method and item.path == request.endpoint_path
                ),
                None,
            )
            if selected_operation is None:
                raise LookupError(f'operation not found: {request.method.upper()} {request.endpoint_path}')
        return ApiContractReadResult(
            root_path=str(self.root_path),
            path=str(path.relative_to(self.root_path)),
            format=self._detect_format(path, spec),
            title=self._extract_title(spec),
            version=self._extract_version(spec),
            servers=self._extract_servers(spec),
            operations=operations,
            selected_operation=selected_operation,
        )

    def _resolve_relative_path(self, raw_path: str) -> Path:
        """把相对路径解析到仓库根目录内，并阻止越界访问。"""
        relative = Path(raw_path)
        candidate = relative.resolve() if relative.is_absolute() else (self.root_path / relative).resolve()
        if candidate != self.root_path and self.root_path not in candidate.parents:
            raise PermissionError(f'path escapes repository root: {raw_path}')
        if not candidate.exists():
            raise FileNotFoundError(raw_path)
        return candidate

    def _load_contract_spec(self, path: Path) -> dict[str, Any] | None:
        """读取并校验 OpenAPI 或 Swagger 契约文件内容。"""
        try:
            raw = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            return None
        loaded: Any
        if path.suffix.lower() == '.json':
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError:
                return None
        else:
            try:
                import yaml  # type: ignore

                loaded = yaml.safe_load(raw)
            except Exception:
                return None
        if not isinstance(loaded, dict):
            return None
        if 'openapi' not in loaded and 'swagger' not in loaded:
            return None
        if not isinstance(loaded.get('paths'), dict):
            return None
        return loaded

    def _detect_format(self, path: Path, spec: dict[str, Any]) -> str:
        """根据文档内容和后缀推断契约规范类型。"""
        if 'openapi' in spec:
            return 'openapi'
        if 'swagger' in spec:
            return 'swagger'
        return path.suffix.lower().lstrip('.')

    def _extract_title(self, spec: dict[str, Any]) -> str | None:
        """提取契约文档标题信息。"""
        info = spec.get('info')
        if isinstance(info, dict):
            title = info.get('title')
            if isinstance(title, str) and title.strip():
                return title.strip()
        return None

    def _extract_version(self, spec: dict[str, Any]) -> str | None:
        """提取契约版本号，必要时回退到规范版本字段。"""
        info = spec.get('info')
        if isinstance(info, dict):
            version = info.get('version')
            if isinstance(version, str) and version.strip():
                return version.strip()
        for key in ('openapi', 'swagger'):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _extract_servers(self, spec: dict[str, Any]) -> list[str]:
        """提取契约中声明的服务端地址列表。"""
        servers = spec.get('servers')
        if not isinstance(servers, list):
            return []
        result: list[str] = []
        for item in servers:
            if not isinstance(item, dict):
                continue
            url = item.get('url')
            if isinstance(url, str) and url.strip():
                result.append(url.strip())
        return result

    def _extract_operations(self, spec: dict[str, Any]) -> list[ApiContractOperation]:
        """展开契约中的全部接口操作并转成统一模型。"""
        paths = spec.get('paths')
        if not isinstance(paths, dict):
            return []
        operations: list[ApiContractOperation] = []
        for endpoint_path, path_item in paths.items():
            if not isinstance(endpoint_path, str) or not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in self.SUPPORTED_METHODS or not isinstance(operation, dict):
                    continue
                tags = operation.get('tags')
                operations.append(
                    ApiContractOperation(
                        method=method,
                        path=endpoint_path,
                        operation_id=operation.get('operationId') if isinstance(operation.get('operationId'), str) else None,
                        summary=operation.get('summary') if isinstance(operation.get('summary'), str) else None,
                        description=(
                            operation.get('description') if isinstance(operation.get('description'), str) else None
                        ),
                        tags=[item for item in tags if isinstance(item, str)] if isinstance(tags, list) else [],
                    )
                )
        return operations


def build_api_contract_capability(root_path: Path | None = None) -> ApiContractCapability:
    """构建默认本地 API Contract capability。"""
    return LocalApiContractCapability((root_path or Path.cwd()).resolve())

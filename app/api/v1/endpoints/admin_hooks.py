"""Hook 管理 API。

管理 .lania/hooks/*.json 生命周期的 CRUD、启用/禁用和批量刷新。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import HookCreateRequest, HookResponse, HookUpdateRequest

router = APIRouter(prefix="/admin/hooks", tags=["admin"])

_HOOKS_DIR = Path(".lania") / "hooks"


def get_container():
    from fastapi import Request

    async def _get(request: Request) -> AppContainer:
        return request.app.state.container
    return _get


def _file_path(name: str) -> Path:
    return _HOOKS_DIR / f"{name}.json"


def _read_hook(name: str) -> HookResponse | None:
    fpath = _file_path(name)
    if not fpath.exists():
        return None
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        return HookResponse(
            name=data.get("name", name),
            description=data.get("description", ""),
            events=data.get("events", []),
            conditions=data.get("conditions", {}),
            actions=data.get("actions", []),
            enabled=data.get("enabled", True),
        )
    except (json.JSONDecodeError, ValueError):
        return None


@router.get("", response_model=list[HookResponse])
async def list_hooks(
    _: None = Depends(RequirePermission("admin.hooks")),
) -> list[HookResponse]:
    """列出所有 Hook。"""
    if not _HOOKS_DIR.exists():
        return []
    hooks: list[HookResponse] = []
    for fpath in sorted(_HOOKS_DIR.glob("*.json")):
        hook = _read_hook(fpath.stem)
        if hook:
            hooks.append(hook)
    return hooks


@router.post("", response_model=HookResponse, status_code=201)
async def create_hook(
    request: HookCreateRequest,
    _: None = Depends(RequirePermission("admin.hooks")),
) -> HookResponse:
    """创建 Hook。"""
    fpath = _file_path(request.name)
    if fpath.exists():
        raise HTTPException(status_code=409, detail=f"Hook '{request.name}' already exists")

    data = {
        "name": request.name,
        "description": request.description,
        "events": request.events,
        "conditions": request.conditions,
        "actions": request.actions,
        "enabled": True,
    }
    fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return HookResponse(
        name=request.name,
        description=request.description,
        events=request.events,
        conditions=request.conditions,
        actions=request.actions,
        enabled=True,
    )


@router.get("/{name}", response_model=HookResponse)
async def get_hook(
    name: str,
    _: None = Depends(RequirePermission("admin.hooks")),
) -> HookResponse:
    """获取单个 Hook。"""
    result = _read_hook(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")
    return result


@router.put("/{name}", response_model=HookResponse)
async def update_hook(
    name: str,
    request: HookUpdateRequest,
    _: None = Depends(RequirePermission("admin.hooks")),
) -> HookResponse:
    """更新 Hook。"""
    fpath = _file_path(name)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")

    current = _read_hook(name)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")

    data = {
        "name": name,
        "description": request.description if request.description is not None else current.description,
        "events": request.events if request.events is not None else current.events,
        "conditions": request.conditions if request.conditions is not None else current.conditions,
        "actions": request.actions if request.actions is not None else [a.model_dump() if hasattr(a, 'model_dump') else a for a in current.actions],
        "enabled": True,
    }
    fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return _read_hook(name) or HookResponse(name=name, events=[], actions=[])


@router.delete("/{name}")
async def delete_hook(
    name: str,
    _: None = Depends(RequirePermission("admin.hooks")),
) -> dict:
    """删除 Hook。"""
    fpath = _file_path(name)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")
    fpath.unlink()
    return {"status": "ok", "name": name}


@router.post("/reload")
async def reload_hooks(
    _: None = Depends(RequirePermission("admin.hooks")),
    container: AppContainer = Depends(get_container()),
) -> dict:
    """重新加载所有 Hook（重新扫描 .lania/hooks/ 目录）。"""
    if hasattr(container, 'customization_engine') and container.customization_engine:
        container.customization_engine._sync_hooks()
    return {"status": "ok", "message": "Hooks reloaded"}

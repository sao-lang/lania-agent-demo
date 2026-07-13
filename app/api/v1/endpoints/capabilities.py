"""Capability API。

列出和管理 Agent 的能力。
"""

from __future__ import annotations


from fastapi import APIRouter, Depends

from app.container import AppContainer
from app.models.agent import CapabilityInfo

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def get_container() -> AppContainer:
    from fastapi import Request

    async def _get(request: Request) -> AppContainer:
        return request.app.state.container

    return _get


@router.get("")
async def list_capabilities(
    container: AppContainer = Depends(get_container()),
) -> list[CapabilityInfo]:
    """列出所有 Capability。"""
    return container.capability_registry.list()


@router.get("/{name}")
async def get_capability(
    name: str,
    container: AppContainer = Depends(get_container()),
) -> CapabilityInfo:
    """查看单个 Capability 详情。"""
    cap = container.capability_registry.get(name)
    if cap is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")

    return CapabilityInfo(
        name=cap.name,
        display_name=cap.display_name,
        description=cap.description,
        enabled=cap.enabled,
        requires=list(cap.requires),
        is_default=cap.is_default,
    )


@router.post("/{name}/enable")
async def enable_capability(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    """启用 Capability。"""
    cap = container.capability_registry.get(name)
    if cap is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")
    cap.enabled = True
    return {"status": "ok", "capability": name, "enabled": True}


@router.post("/{name}/disable")
async def disable_capability(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    """禁用 Capability。"""
    cap = container.capability_registry.get(name)
    if cap is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")
    cap.enabled = False
    return {"status": "ok", "capability": name, "enabled": False}

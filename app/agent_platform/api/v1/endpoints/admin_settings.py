"""系统设置管理 API�?""

from fastapi import APIRouter, Depends

from app.container import AppContainer
from app.agent_platform.core.auth import RequirePermission
from app.models.admin import SystemSettings

router = APIRouter(prefix="/admin/settings", tags=["admin"])


def get_container():
    from fastapi import Request

    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.get("")
async def get_all_settings(
    _: None = Depends(RequirePermission("admin.settings")),
    container: AppContainer = Depends(get_container()),
) -> SystemSettings:
    return container.system_settings_manager.get_all()


@router.get("/{key}")
async def get_setting(
    key: str,
    container: AppContainer = Depends(get_container()),
):
    value = container.system_settings_manager.get(key)
    return {key: value}


@router.put("/{key}")
async def set_setting(
    key: str, value,
    container: AppContainer = Depends(get_container()),
):
    container.system_settings_manager.set(key, value)
    return {"status": "ok", key: value}

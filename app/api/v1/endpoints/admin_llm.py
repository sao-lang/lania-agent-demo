"""LLM 配置管理 API。"""

from fastapi import APIRouter, Depends

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import LlmProviderConfig, LlmProviderStatus

router = APIRouter(prefix="/admin/llm", tags=["admin"])


def get_container():
    from fastapi import Request
    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.get("/providers")
async def list_providers(
    _: None = Depends(RequirePermission("admin.llm")),
    container: AppContainer = Depends(get_container()),
) -> list[LlmProviderConfig]:
    return await container.llm_config_manager.list_providers()


@router.post("/providers")
async def set_provider(
    config: LlmProviderConfig,
    container: AppContainer = Depends(get_container()),
):
    await container.llm_config_manager.set_provider(config)
    return {"status": "ok", "provider": config.name}


@router.delete("/providers/{name}")
async def delete_provider(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    await container.llm_config_manager.delete_provider(name)
    return {"status": "ok"}


@router.post("/providers/{name}/test")
async def test_provider(
    name: str,
    container: AppContainer = Depends(get_container()),
) -> LlmProviderStatus:
    return await container.llm_config_manager.test_connection(name)


@router.get("/active")
async def get_active(
    container: AppContainer = Depends(get_container()),
) -> LlmProviderConfig | None:
    return await container.llm_config_manager.get_active()


@router.put("/active")
async def set_active(
    name: str, model: str,
    container: AppContainer = Depends(get_container()),
):
    await container.llm_config_manager.set_active(name, model)
    return {"status": "ok", "active_provider": name, "model": model}


# ── 按用途路由 ───────────────────────────────

@router.get("/routes")
async def list_routes(
    container: AppContainer = Depends(get_container()),
):
    """列出所有用途的 LLM 路由。"""
    return container.llm_config_manager.list_routes()


@router.get("/routes/{purpose}")
async def get_route(
    purpose: str,
    container: AppContainer = Depends(get_container()),
):
    """获取单个用途的路由。"""
    route = container.llm_config_manager.get_route(purpose)
    if route is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404, detail=f"Unknown purpose: {purpose}",
        )
    return {"purpose": purpose, **route}


@router.put("/routes/{purpose}")
async def set_route(
    purpose: str, provider: str, model: str,
    container: AppContainer = Depends(get_container()),
):
    """设置单个用途的路由。"""
    container.llm_config_manager.set_route(purpose, provider, model)
    return {
        "status": "ok", "purpose": purpose,
        "provider": provider, "model": model,
    }


@router.post("/routes/{purpose}/reset")
async def reset_route(
    purpose: str,
    container: AppContainer = Depends(get_container()),
):
    """恢复用途路由到默认值。"""
    container.llm_config_manager.reset_route(purpose)
    return {"status": "ok", "purpose": purpose}

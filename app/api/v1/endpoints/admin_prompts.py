"""提示词模板管理 API。"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import PromptTemplate

router = APIRouter(prefix="/admin/prompts", tags=["admin"])


def get_container():
    from fastapi import Request
    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.get("")
async def list_prompts(
    _: None = Depends(RequirePermission("admin.prompts")),
    container: AppContainer = Depends(get_container()),
) -> list[PromptTemplate]:
    return await container.prompt_manager.list()


@router.get("/{name}")
async def get_prompt(
    name: str,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    tpl = await container.prompt_manager.get(name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return tpl


@router.put("/{name}")
async def update_prompt(
    name: str, template: str,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    return await container.prompt_manager.update(name, template)


@router.post("/{name}/reset")
async def reset_prompt(
    name: str,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    tpl = await container.prompt_manager.reset(name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return tpl

"""提示词模板管理 API。"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import PromptCreateRequest, PromptTemplate

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
    """列出所有 Prompt 模板（内置 + 自定义）。"""
    return await container.prompt_manager.list()


@router.post("")
async def create_prompt(
    request: PromptCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    """创建 Prompt 模板。"""
    return await container.prompt_manager.create(request)


@router.get("/{prompt_id}")
async def get_prompt(
    prompt_id: str,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    """按 id 获取 Prompt 模板。"""
    tpl = await container.prompt_manager.get(prompt_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{prompt_id}' not found")
    return tpl


@router.put("/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    request: PromptCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    """更新 Prompt 模板（version 递增）。"""
    try:
        return await container.prompt_manager.update(prompt_id, request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    container: AppContainer = Depends(get_container()),
):
    """删除 Prompt 模板。"""
    await container.prompt_manager.delete(prompt_id)
    return {"status": "ok"}


@router.post("/{prompt_id}/reset")
async def reset_prompt(
    prompt_id: str,
    container: AppContainer = Depends(get_container()),
) -> PromptTemplate:
    """恢复 Prompt 模板到内置默认。"""
    tpl = await container.prompt_manager.reset(prompt_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{prompt_id}' not found")
    return tpl


@router.post("/render")
async def render_prompt(
    name: str,
    variables: dict,
    container: AppContainer = Depends(get_container()),
) -> dict:
    """按 name 渲染 Prompt 模板（运行时按需调用）。"""
    result = await container.prompt_manager.render(name, **variables)
    return {"result": result}
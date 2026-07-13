"""Skill 管理 API。

支持 JSON 和文件两种输入方式，统一持久化到 SQLite。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import SkillCreateRequest, SkillDefinition

router = APIRouter(prefix="/admin/skills", tags=["admin"])


def get_container():
    from fastapi import Request

    async def _get(request: Request):
        return request.app.state.container

    return _get


class ImportRequest(BaseModel):
    """Skill 导入请求。"""
    path: str | None = None       # 文件目录路径（文件导入）
    format: str = "json"          # "json" | "file"
    data: dict | None = None      # JSON 格式数据


@router.get("")
async def list_skills(
    _: None = Depends(RequirePermission("admin.skills")),
    container: AppContainer = Depends(get_container()),
) -> list[SkillDefinition]:
    """列出所有 Skill（含 rules）。"""
    return await container.skill_manager.list()


@router.post("")
async def create_skill(
    request: SkillCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> SkillDefinition:
    """创建 Skill（JSON 方式）。"""
    return await container.skill_manager.create(request)


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    container: AppContainer = Depends(get_container()),
) -> SkillDefinition:
    """按 id 获取 Skill（含 rules）。"""
    skill = await container.skill_manager.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return skill


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str,
    request: SkillCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> SkillDefinition:
    """更新 Skill（覆盖模式，version 递增）。"""
    try:
        return await container.skill_manager.update(skill_id, request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    container: AppContainer = Depends(get_container()),
):
    """删除 Skill 及其关联的全部 rules。"""
    await container.skill_manager.delete(skill_id)
    return {"status": "ok"}


@router.post("/import")
async def import_skill(
    request: ImportRequest,
    container: AppContainer = Depends(get_container()),
) -> SkillDefinition:
    """导入 Skill（文件目录或 JSON 格式）。

    文件导入: {"path": ".lania/skills/ai-coding-rules"}
    JSON 导入: {"format": "json", "data": {...}}
    """
    if request.path:
        return await container.skill_manager.import_from_dir(request.path)
    elif request.format == "json" and request.data:
        req = SkillCreateRequest(**request.data)
        return await container.skill_manager.create(req)
    else:
        raise HTTPException(status_code=400, detail="Invalid import request")

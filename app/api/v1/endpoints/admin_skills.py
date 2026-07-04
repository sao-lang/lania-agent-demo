"""Skill 管理 API。"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import SkillDefinition

router = APIRouter(prefix="/admin/skills", tags=["admin"])


def get_container():
    from fastapi import Request
    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.get("")
async def list_skills(
    _: None = Depends(RequirePermission("admin.skills")),
    container: AppContainer = Depends(get_container()),
) -> list[SkillDefinition]:
    return await container.skill_manager.list_skills()


@router.post("")
async def register_skill(
    skill: SkillDefinition,
    container: AppContainer = Depends(get_container()),
):
    await container.skill_manager.register_skill(skill)
    return {"status": "ok", "skill": skill.name}


@router.get("/{name}")
async def get_skill(
    name: str,
    container: AppContainer = Depends(get_container()),
) -> SkillDefinition:
    skill = await container.skill_manager.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill


@router.put("/{name}")
async def update_skill(
    name: str, skill: SkillDefinition,
    container: AppContainer = Depends(get_container()),
):
    await container.skill_manager.register_skill(skill)
    return {"status": "ok", "skill": name}


@router.delete("/{name}")
async def delete_skill(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    await container.skill_manager.remove_skill(name)
    return {"status": "ok"}


@router.post("/load")
async def load_skill_from_file(
    path: str,
    container: AppContainer = Depends(get_container()),
) -> SkillDefinition:
    skill = await container.skill_manager.load_from_file(path)
    await container.skill_manager.register_skill(skill)
    return skill

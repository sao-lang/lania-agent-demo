"""Agent 定义管理 API。"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import AgentDefinition

router = APIRouter(prefix="/admin/agents", tags=["admin"])


def get_container():
    from fastapi import Request
    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.get("")
async def list_agents(
    _: None = Depends(RequirePermission("admin.agents")),
    container: AppContainer = Depends(get_container()),
) -> list[AgentDefinition]:
    return await container.agent_def_manager.list()


@router.post("")
async def create_agent(
    agent: AgentDefinition,
    container: AppContainer = Depends(get_container()),
):
    await container.agent_def_manager.create(agent)
    return {"status": "ok", "agent": agent.name}


@router.get("/{name}")
async def get_agent(
    name: str,
    container: AppContainer = Depends(get_container()),
) -> AgentDefinition:
    agent = await container.agent_def_manager.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return agent


@router.put("/{name}")
async def update_agent(
    name: str, agent: AgentDefinition,
    container: AppContainer = Depends(get_container()),
):
    await container.agent_def_manager.update(name, agent)
    return {"status": "ok", "agent": name}


@router.delete("/{name}")
async def delete_agent(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    await container.agent_def_manager.delete(name)
    return {"status": "ok"}


@router.post("/{name}/activate")
async def activate_agent(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    await container.agent_def_manager.set_default(name)
    return {"status": "ok", "default_agent": name}

"""Agent 定义管理 API。"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import AgentCreateRequest, AgentDefinition

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
    """列出所有 Agent 定义。"""
    return await container.agent_def_manager.list()


@router.post("")
async def create_agent(
    request: AgentCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> AgentDefinition:
    """创建 Agent 定义。"""
    return await container.agent_def_manager.create(request)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    container: AppContainer = Depends(get_container()),
) -> AgentDefinition:
    """按 id 获取 Agent 定义。"""
    agent = await container.agent_def_manager.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return agent


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    request: AgentCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> AgentDefinition:
    """更新 Agent 定义（version 递增）。"""
    try:
        return await container.agent_def_manager.update(agent_id, request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    container: AppContainer = Depends(get_container()),
):
    """删除 Agent 定义。"""
    await container.agent_def_manager.delete(agent_id)
    return {"status": "ok"}


@router.post("/{agent_id}/activate")
async def activate_agent(
    agent_id: str,
    container: AppContainer = Depends(get_container()),
):
    """设为默认 Agent。"""
    await container.agent_def_manager.set_default(agent_id)
    return {"status": "ok", "default_agent": agent_id}

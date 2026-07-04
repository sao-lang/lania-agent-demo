"""MCP 配置管理 API。"""

from fastapi import APIRouter, Depends

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.services.mcp_manager import McpServerStatus

router = APIRouter(prefix="/admin/mcp", tags=["admin"])


def get_container():
    from fastapi import Request
    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.post("/connect")
async def connect_mcp(
    _: None = Depends(RequirePermission("admin.mcp")),
    config: dict,
    container: AppContainer = Depends(get_container()),
):
    tools = await container.mcp_manager.connect(config)
    return {
        "status": "ok",
        "tools_count": len(tools),
        "tools": [{"server": t.server, "name": t.name} for t in tools],
    }


@router.post("/disconnect")
async def disconnect_mcp(
    name: str,
    container: AppContainer = Depends(get_container()),
):
    await container.mcp_manager.disconnect(name)
    return {"status": "ok"}


@router.get("/servers")
async def list_servers(
    container: AppContainer = Depends(get_container()),
) -> list[McpServerStatus]:
    return await container.mcp_manager.list_servers()


@router.get("/tools")
async def list_mcp_tools(
    container: AppContainer = Depends(get_container()),
):
    tools = await container.mcp_manager.list_tools()
    return [
        {"server": t.server, "name": t.name, "description": t.description}
        for t in tools
    ]

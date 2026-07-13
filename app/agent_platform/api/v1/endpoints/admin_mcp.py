"""MCP 配置管理 API�?
支持 MCP Server 配置�?CRUD 和连接管理�?"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.models.admin import McpServerConfig, McpServerCreateRequest
from app.agent_platform.services.mcp_manager import McpServerStatus

router = APIRouter(prefix="/admin/mcp", tags=["admin"])


def get_container():
    from fastapi import Request

    async def _get(request: Request):
        return request.app.state.container

    return _get


# ── MCP Server 配置 CRUD ────────────────────

@router.get("/servers")
async def list_servers_config(
    container: AppContainer = Depends(get_container()),
) -> list[McpServerConfig]:
    """列出所�?MCP Server 配置（从 DB）�?""
    return await container.mcp_manager.list_servers_config()


@router.post("/servers")
async def create_server(
    request: McpServerCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> McpServerConfig:
    """创建 MCP Server 配置�?""
    return await container.mcp_manager.create_server(request)


@router.get("/servers/{mcp_id}")
async def get_server(
    mcp_id: str,
    container: AppContainer = Depends(get_container()),
) -> McpServerConfig:
    """�?id 获取 MCP Server 配置�?""
    config = await container.mcp_manager.get_server(mcp_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{mcp_id}' not found")
    return config


@router.put("/servers/{mcp_id}")
async def update_server(
    mcp_id: str,
    request: McpServerCreateRequest,
    container: AppContainer = Depends(get_container()),
) -> McpServerConfig:
    """更新 MCP Server 配置�?""
    try:
        return await container.mcp_manager.update_server(mcp_id, request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/servers/{mcp_id}")
async def delete_server(
    mcp_id: str,
    container: AppContainer = Depends(get_container()),
):
    """删除 MCP Server 配置�?""
    await container.mcp_manager.delete_server(mcp_id)
    return {"status": "ok"}


# ── 连接管理 ────────────────────────────────

@router.post("/servers/{mcp_id}/connect")
async def connect_server(
    mcp_id: str,
    container: AppContainer = Depends(get_container()),
):
    """连接指定 MCP Server�?""
    tools = await container.mcp_manager.get_or_connect(mcp_id)
    return {
        "status": "ok",
        "tools_count": len(tools),
        "tools": [{"server": t.server, "name": t.name} for t in tools],
    }


@router.post("/servers/{mcp_id}/disconnect")
async def disconnect_server(
    mcp_id: str,
    container: AppContainer = Depends(get_container()),
):
    """断开指定 MCP Server�?""
    config = await container.mcp_manager.get_server(mcp_id)
    if config:
        await container.mcp_manager.disconnect(config.name)
    return {"status": "ok"}


@router.post("/connect")
async def connect_mcp(
    config: dict,
    container: AppContainer = Depends(get_container()),
):
    """直接连接 MCP Server（透传配置）�?""
    tools = await container.mcp_manager.connect(config)
    return {
        "status": "ok",
        "tools_count": len(tools),
        "tools": [{"server": t.server, "name": t.name} for t in tools],
    }


@router.get("/status")
async def list_servers_status(
    container: AppContainer = Depends(get_container()),
) -> list[McpServerStatus]:
    """列出所有已连接 MCP Server 的运行状态�?""
    return await container.mcp_manager.list_servers()


@router.get("/tools")
async def list_mcp_tools(
    container: AppContainer = Depends(get_container()),
):
    """列出所有已连接�?MCP 工具�?""
    tools = await container.mcp_manager.list_tools()
    return [
        {"server": t.server, "name": t.name, "description": t.description}
        for t in tools
    ]

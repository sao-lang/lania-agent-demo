"""MCP 客户端管理器。

管理后端的 MCP 客户端连接，支持：
- 连接外部 MCP Server（URL / STDIO）
- 工具发现和注册
- 工具调用路由
- 持久化 Server 配置
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.admin import McpServerConfig, McpServerCreateRequest
from app.services.sqlite_store import SQLiteStateStore


class McpToolDef(BaseModel):
    """MCP 工具定义。"""

    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpServerStatus(BaseModel):
    """MCP Server 连接状态。"""

    name: str
    server_type: str  # url | stdio
    status: str  # connected | disconnected | error
    tools_count: int = 0
    error: str | None = None


class McpManager:
    """MCP 客户端管理器。

    负责：
    - 管理 MCP Server 配置的 CRUD（持久化到 SQLite）
    - 连接/断开外部 MCP Server
    - 列出可用工具、调用工具
    """

    def __init__(self, persistence: SQLiteStateStore | None = None) -> None:
        self._persistence = persistence
        self._servers: dict[str, Any] = {}         # 运行时连接状态
        self._tools: dict[str, McpToolDef] = {}    # 已注册的工具

    # ── 配置 CRUD（持久化）────────────────────

    async def create_server(self, request: McpServerCreateRequest) -> McpServerConfig:
        """创建 MCP Server 配置。"""
        now = datetime.now()
        config = McpServerConfig(
            id=f"mcp-{uuid4().hex[:12]}",
            name=request.name,
            server_type=request.server_type,
            url=request.url,
            command=request.command,
            args=request.args,
            enabled=request.enabled,
            status="disconnected",
            created_at=now,
            updated_at=now,
        )
        if self._persistence:
            self._persistence.upsert_mcp_server(config.model_dump(mode="json"))
        return config

    async def update_server(self, mcp_id: str, request: McpServerCreateRequest) -> McpServerConfig:
        """更新 MCP Server 配置。"""
        existing = await self.get_server(mcp_id)
        if existing is None:
            raise ValueError(f"MCP server '{mcp_id}' not found")

        now = datetime.now()
        config = McpServerConfig(
            id=mcp_id,
            name=request.name,
            server_type=request.server_type,
            url=request.url,
            command=request.command,
            args=request.args,
            enabled=request.enabled,
            status=existing.status,
            tools_count=existing.tools_count,
            error=existing.error,
            created_at=existing.created_at,
            updated_at=now,
        )
        if self._persistence:
            self._persistence.upsert_mcp_server(config.model_dump(mode="json"))
        return config

    async def get_server(self, mcp_id: str) -> McpServerConfig | None:
        """按 id 获取 MCP Server 配置。"""
        if self._persistence is None:
            return None
        payload = self._persistence.get_mcp_server(mcp_id)
        if payload is None:
            return None
        return McpServerConfig(**payload)

    async def list_servers_config(self) -> list[McpServerConfig]:
        """列出所有 MCP Server 配置（从 DB）。"""
        if self._persistence is None:
            return []
        payloads = self._persistence.list_mcp_servers()
        return [McpServerConfig(**p) for p in payloads]

    async def delete_server(self, mcp_id: str) -> None:
        """删除 MCP Server 配置。"""
        config = await self.get_server(mcp_id)
        if config:
            await self.disconnect(config.name)
        if self._persistence:
            self._persistence.delete_mcp_server(mcp_id)

    # ── 连接管理 ──────────────────────────────

    async def connect(self, config: dict) -> list[McpToolDef]:
        """根据配置连接到 MCP Server。

        Args:
            config: MCP 配置，格式:
                {
                    "mcpServers": {
                        "server-name": {
                            "type": "url" | "stdio",
                            "url": "...",
                            "command": "...",
                            "args": [...],
                        }
                    }
                }

        Returns:
            所有连接的 Server 提供的工具列表。
        """
        tools: list[McpToolDef] = []
        servers_config = config.get("mcpServers", {})

        for name, sc in servers_config.items():
            server_type = sc.get("type", "url")
            try:
                if server_type == "url":
                    await self._connect_url(name, sc["url"])
                elif server_type == "stdio":
                    await self._connect_stdio(
                        name, sc.get("command", ""), sc.get("args", []),
                    )
                else:
                    continue

                # 列出该 Server 的工具
                server_tools = await self._list_server_tools(name)
                for t in server_tools:
                    self._tools[f"{name}:{t.name}"] = t
                tools.extend(server_tools)

            except Exception as e:
                self._servers[name] = {"status": "error", "error": str(e)}

        return tools

    async def get_or_connect(self, mcp_id: str) -> list[McpToolDef]:
        """按 ID 加载 MCP 配置并连接（如果尚未连接）。"""
        config = await self.get_server(mcp_id)
        if not config:
            raise ValueError(f"MCP server '{mcp_id}' not found")

        if config.name in self._servers:
            return [
                t for t in self._tools.values()
                if t.server == config.name
            ]

        return await self.connect({
            "mcpServers": {
                config.name: {
                    "type": config.server_type,
                    "url": config.url,
                    "command": config.command,
                    "args": config.args,
                }
            }
        })

    async def disconnect(self, name: str) -> None:
        """断开 MCP Server 连接。"""
        if name in self._servers:
            keys_to_remove = [
                k for k in self._tools if k.startswith(f"{name}:")
            ]
            for k in keys_to_remove:
                del self._tools[k]
            del self._servers[name]

    async def disconnect_all(self) -> None:
        """断开所有 MCP Server。"""
        for name in list(self._servers.keys()):
            await self.disconnect(name)

    async def list_servers(self) -> list[McpServerStatus]:
        """列出所有已连接的 MCP Server 及状态。"""
        statuses: list[McpServerStatus] = []
        for name, server in self._servers.items():
            tools_count = sum(
                1 for k in self._tools if k.startswith(f"{name}:")
            )
            statuses.append(McpServerStatus(
                name=name,
                server_type=server.get("type", "url"),
                status=server.get("status", "connected"),
                tools_count=tools_count,
                error=server.get("error"),
            ))
        return statuses

    async def list_tools(self) -> list[McpToolDef]:
        """列出所有已连接的 MCP 工具。"""
        return list(self._tools.values())

    async def call_tool(self, tool_name: str, args: dict) -> Any:
        """调用 MCP 工具。"""
        for key, tool in self._tools.items():
            if tool.name == tool_name:
                server_name = key.split(":", 1)[0]
                return await self._call_server_tool(server_name, tool_name, args)
        raise ValueError(f"Tool '{tool_name}' not found")

    # ── 内部实现 ──────────────────────────────

    async def _connect_url(self, name: str, url: str) -> None:
        """通过 URL 连接 MCP Server (SSE)。"""
        # TODO: 实现 SSE MCP 客户端连接
        self._servers[name] = {
            "type": "url", "url": url, "status": "connected",
        }

    async def _connect_stdio(self, name: str, command: str, args: list[str]) -> None:
        """通过 STDIO 连接 MCP Server。"""
        # TODO: 实现 STDIO MCP 客户端连接
        self._servers[name] = {
            "type": "stdio", "command": command, "args": args, "status": "connected",
        }

    async def _list_server_tools(self, name: str) -> list[McpToolDef]:
        """列出某个 Server 的工具。"""
        # TODO: 通过 MCP 协议 list_tools
        return []

    async def _call_server_tool(self, server: str, tool: str, args: dict) -> Any:
        """调用某个 Server 的工具。"""
        # TODO: 通过 MCP 协议 call_tool
        return {}

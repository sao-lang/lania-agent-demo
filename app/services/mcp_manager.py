"""MCP 客户端管理器。

管理后端的 MCP 客户端连接，支持连接外部 MCP Server 并暴露其工具。
CLI/Web 将 MCP 配置透传给后端，后端统一管理连接生命周期。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpToolDef(BaseModel):
    """MCP 工具定义。"""

    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpServerStatus(BaseModel):
    """MCP Server 连接状态。"""

    name: str
    type: str  # stdio | url
    status: str  # connected | disconnected | error
    tools_count: int = 0
    error: str | None = None


class McpManager:
    """MCP 客户端管理器。

    负责连接/断开外部 MCP Server，列出可用工具，调用工具。
    """

    def __init__(self) -> None:
        self._servers: dict[str, Any] = {}
        self._tools: dict[str, McpToolDef] = {}

    async def connect(self, config: dict) -> list[McpToolDef]:
        """根据配置连接到 MCP Server。

        Args:
            config: MCP 配置，格式:
                {
                    "mcpServers": {
                        "server-name": {
                            "type": "url" | "stdio",
                            "url": "...",  # for type=url
                            "command": "...",  # for type=stdio
                            "args": [...],  # for type=stdio
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
                    await self._connect_stdio(name, sc.get("command", ""), sc.get("args", []))
                else:
                    continue

                # 列出该 Server 的工具
                server_tools = await self._list_server_tools(name)
                for t in server_tools:
                    self._tools[f"{name}:{t.name}"] = t
                tools.extend(server_tools)

            except Exception as e:
                # 单个 Server 连接失败不影响其他
                self._servers[name] = {"status": "error", "error": str(e)}

        return tools

    async def disconnect(self, name: str) -> None:
        """断开 MCP Server 连接。"""
        if name in self._servers:
            # 清理工具
            keys_to_remove = [k for k in self._tools if k.startswith(f"{name}:")]
            for k in keys_to_remove:
                del self._tools[k]
            del self._servers[name]

    async def disconnect_all(self) -> None:
        """断开所有 MCP Server。"""
        for name in list(self._servers.keys()):
            await self.disconnect(name)

    async def list_servers(self) -> list[McpServerStatus]:
        """列出所有 MCP Server 及状态。"""
        statuses: list[McpServerStatus] = []
        for name, server in self._servers.items():
            tools_count = sum(
                1 for k in self._tools if k.startswith(f"{name}:")
            )
            statuses.append(McpServerStatus(
                name=name,
                type=server.get("type", "url"),
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
        # 查找工具所在的 Server
        for key, tool in self._tools.items():
            if tool.name == tool_name:
                server_name = key.split(":", 1)[0]
                return await self._call_server_tool(server_name, tool_name, args)
        raise ValueError(f"Tool '{tool_name}' not found")

    # ── 内部实现 ──────────────────────────────

    async def _connect_url(self, name: str, url: str) -> None:
        """通过 URL 连接 MCP Server (SSE)。"""
        # TODO: 实现 SSE MCP 客户端连接
        self._servers[name] = {"type": "url", "url": url, "status": "connected"}

    async def _connect_stdio(self, name: str, command: str, args: list[str]) -> None:
        """通过 STDIO 连接 MCP Server。"""
        # TODO: 实现 STDIO MCP 客户端连接
        self._servers[name] = {"type": "stdio", "command": command, "args": args, "status": "connected"}

    async def _list_server_tools(self, name: str) -> list[McpToolDef]:
        """列出某个 Server 的工具。"""
        # TODO: 通过 MCP 协议 list_tools
        return []

    async def _call_server_tool(self, server: str, tool: str, args: dict) -> Any:
        """调用某个 Server 的工具。"""
        # TODO: 通过 MCP 协议 call_tool
        return {}

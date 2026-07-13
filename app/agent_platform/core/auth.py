"""认证中间件与权限依赖模块。

提供 API Key 校验中间件和按权限路由的依赖注入函数。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.container import AppContainer
from app.models.auth import Role


# 不需要认证的路径前缀
PUBLIC_PATHS = {
    "/health",
    "/api/v1/health",
    "/api/v1/auth/login",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件。

    拦截所有非公开请求，校验 Authorization header。
    支持两种认证方式:
    1. Bearer Token (Web 登录后)
    2. Bearer API Key (CLI 直接调用)
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # 公开路径放行
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # API 前缀检查
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        # MCP 路径放行（支持外部 MCP 客户端无认证连接）
        if "/mcp" in request.url.path:
            return await call_next(request)

        # 提取 Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        credential = auth_header.split(" ", 1)[1]
        container: AppContainer | None = getattr(
            request.app.state, "container", None,
        )

        if container is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "Service not initialized"},
            )

        # 先校验 Token
        token = await container.auth_manager.validate_token(credential)
        if token is not None:
            request.state.role = token.role
            request.state.authenticated = True
            return await call_next(request)

        # 再校验 API Key
        key_record = await container.auth_manager.validate_api_key(credential)
        if key_record is not None:
            request.state.role = key_record.role
            request.state.authenticated = True
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API Key or token"},
        )


# ── 权限依赖注入 ─────────────────────────────

class RequirePermission:
    """依赖注入：要求用户具有指定权限。

    用法:
        @router.get("/admin/llm/providers")
        async def list_providers(
            _: None = Depends(RequirePermission("admin.llm")),
            container = Depends(get_container()),
        ):
            ...
    """

    def __init__(self, permission: str) -> None:
        self.permission = permission

    async def __call__(self, request: Request) -> None:
        role: Role | None = getattr(request.state, "role", None)
        if role is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        container: AppContainer | None = getattr(
            request.app.state, "container", None,
        )
        if container is None:
            raise HTTPException(
                status_code=503, detail="Service not initialized",
            )

        if not container.auth_manager.check_permission(role, self.permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: required '{self.permission}'",
            )


class RequireRole:
    """依赖注入：要求用户具有指定角色。"""

    def __init__(self, role: Role) -> None:
        self.role = role

    async def __call__(self, request: Request) -> None:
        role: Role | None = getattr(request.state, "role", None)
        if role is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        role_rank = {"admin": 3, "user": 2, "readonly": 1}
        if role_rank.get(role, 0) < role_rank.get(self.role, 0):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{self.role}' or higher required, got '{role}'",
            )

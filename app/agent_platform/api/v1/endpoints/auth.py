"""认证 API。"""

from fastapi import APIRouter, Depends, HTTPException, Header

from app.container import AppContainer
from app.models.auth import LoginRequest, LoginResponse, ProfileResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def get_container():
    from fastapi import Request

    async def _get(request: Request):
        return request.app.state.container

    return _get


@router.post("/login")
async def login(
    request: LoginRequest,
    container: AppContainer = Depends(get_container()),
) -> LoginResponse:
    """用 API Key 登录。"""
    result = await container.auth_manager.login(request.api_key)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return result


@router.post("/verify")
async def verify_token(
    authorization: str = Header(None),
    container: AppContainer = Depends(get_container()),
):
    """验证 Token 有效性。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token_str = authorization.split(" ", 1)[1]
    token = await container.auth_manager.validate_token(token_str)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"valid": True, "role": token.role}


@router.get("/profile")
async def get_profile(
    authorization: str = Header(None),
    container: AppContainer = Depends(get_container()),
) -> ProfileResponse:
    """获取当前用户信息。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token_str = authorization.split(" ", 1)[1]
    profile = await container.auth_manager.get_profile(token_str)
    if profile is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return profile

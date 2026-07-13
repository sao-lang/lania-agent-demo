"""认证与权限模型模块。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["admin", "user", "readonly"]


class ApiKeyRecord(BaseModel):
    """API Key 记录。"""
    id: str
    key_hash: str
    name: str
    role: Role = "user"
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now)


class AuthToken(BaseModel):
    """登录后颁发的会话 Token。"""
    token: str
    key_id: str
    role: Role
    expires_at: datetime


class LoginRequest(BaseModel):
    """登录请求。"""
    api_key: str


class LoginResponse(BaseModel):
    """登录响应。"""
    token: str
    role: Role
    name: str
    expires_at: datetime


class ProfileResponse(BaseModel):
    """用户信息响应。"""
    name: str
    role: Role
    permissions: list[str]

"""认证与权限管理模块�?

管理 API Key 的校验、Token 颁发、角色权限判定�?
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.models.auth import (
    ApiKeyRecord,
    AuthToken,
    LoginResponse,
    ProfileResponse,
    Role,
)
from app.agent_platform.services.config_store import ConfigStore


# 权限定义：角�?�?可访问的资源
ROLE_PERMISSIONS: dict[Role, list[str]] = {
    "admin": [
        "agent.chat", "agent.plan", "agent.autopilot",
        "task.*", "document.*", "collection.*",
        "admin.llm", "admin.skills", "admin.agents",
        "admin.prompts", "admin.mcp", "admin.settings",
        "admin.users", "admin.auth",
    ],
    "user": [
        "agent.chat", "agent.plan", "agent.autopilot",
        "task.*", "document.*", "collection.*",
    ],
    "readonly": [
        "agent.chat",
        "task.read", "collection.read",
    ],
}


class AuthManager:
    """认证管理器�?""

    _NAMESPACE = "auth"
    _TOKEN_EXPIRY_HOURS = 24
    _DEV_DEFAULT_KEY = "dev-key-123"

    def __init__(self, config_store: ConfigStore) -> None:
        self._store = config_store
        self._tokens: dict[str, AuthToken] = {}  # 内存 Token 缓存

    # ── API Key 管理 ─────────────────────────

    async def validate_api_key(self, api_key: str) -> ApiKeyRecord | None:
        """验证 API Key 是否有效�?""
        # 开发环境默�?Key
        if api_key == self._DEV_DEFAULT_KEY:
            return ApiKeyRecord(
                id="dev", key_hash=self._hash_key(api_key),
                name="Developer", role="admin",
            )

        # �?SQLite 查询
        key_hash = self._hash_key(api_key)
        value = self._store.get(self._NAMESPACE, key_hash)
        if value and isinstance(value, dict):
            record = ApiKeyRecord(**value)
            if record.enabled:
                return record
        return None

    async def create_api_key(
        self, name: str, role: Role = "user",
    ) -> tuple[str, ApiKeyRecord]:
        """创建新的 API Key�?""
        raw_key = f"lan-{secrets.token_hex(24)}"
        key_hash = self._hash_key(raw_key)
        record = ApiKeyRecord(
            id=str(len(raw_key)),
            key_hash=key_hash,
            name=name,
            role=role,
        )
        self._store.set(
            self._NAMESPACE, key_hash, record.model_dump(),
        )
        return raw_key, record

    async def list_api_keys(self) -> list[ApiKeyRecord]:
        """列出所�?API Key�?""
        items = self._store.list(self._NAMESPACE)
        keys: list[ApiKeyRecord] = []
        for item in items:
            if isinstance(item.value, dict):
                keys.append(ApiKeyRecord(**item.value))
        return keys

    async def delete_api_key(self, key_hash: str) -> None:
        """删除 API Key�?""
        self._store.delete(self._NAMESPACE, key_hash)

    # ── Token 管理 ───────────────────────────

    async def login(self, api_key: str) -> LoginResponse | None:
        """�?API Key 登录，颁�?Token�?""
        record = await self.validate_api_key(api_key)
        if record is None:
            return None

        token_str = secrets.token_hex(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=self._TOKEN_EXPIRY_HOURS,
        )
        token = AuthToken(
            token=token_str,
            key_id=record.id,
            role=record.role,
            expires_at=expires_at,
        )
        self._tokens[token_str] = token
        return LoginResponse(
            token=token_str,
            role=record.role,
            name=record.name,
            expires_at=expires_at,
        )

    async def validate_token(self, token_str: str) -> AuthToken | None:
        """验证 Token 是否有效�?""
        token = self._tokens.get(token_str)
        if token is None:
            return None
        if token.expires_at < datetime.now(timezone.utc):
            self._tokens.pop(token_str, None)
            return None
        return token

    async def get_profile(self, token_str: str) -> ProfileResponse | None:
        """获取当前用户信息�?""
        token = await self.validate_token(token_str)
        if token is None:
            return None
        return ProfileResponse(
            name=token.key_id,
            role=token.role,
            permissions=ROLE_PERMISSIONS.get(token.role, []),
        )

    # ── 权限校验 ─────────────────────────────

    def check_permission(self, role: Role, required: str) -> bool:
        """检查角色是否有指定权限�?""
        permissions = ROLE_PERMISSIONS.get(role, [])
        for p in permissions:
            if p == required:
                return True
            if p.endswith(".*"):
                prefix = p[:-2]
                if required.startswith(prefix):
                    return True
        return False

    # ── 内部方法 ─────────────────────────────

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

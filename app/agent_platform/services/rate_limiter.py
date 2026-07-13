"""多维度速率限制服务。

限制维度：
- per_user: 单用户每分钟最大请求数
- per_agent: 单 Agent 每分钟最大请求数
- per_tenant: 单租户每小时最大 token 数

使用滑动窗口算法。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RateLimitConfig:
    """速率限制配置。"""
    max_requests: int = 100
    window_seconds: int = 60


@dataclass
class RateLimitResult:
    """速率检查结果。"""
    allowed: bool = True
    retry_after: float = 0.0
    reason: str = ""


class SlidingWindow:
    """滑动窗口计数器。"""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []

    def allow(self) -> tuple[bool, float]:
        """检查是否允许此次请求。

        Returns:
            (allowed, retry_after_seconds)
        """
        now = time.time()
        cutoff = now - self.window_seconds
        # 移除窗口外的记录
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        if len(self._timestamps) >= self.max_requests:
            retry_after = self._timestamps[0] + self.window_seconds - now
            return False, max(retry_after, 0.0)

        self._timestamps.append(now)
        return True, 0.0


class RateLimiter:
    """多维度速率限制器。"""

    def __init__(self) -> None:
        self._limits: dict[str, RateLimitConfig] = {
            "per_user":   RateLimitConfig(max_requests=100, window_seconds=60),
            "per_agent":  RateLimitConfig(max_requests=1000, window_seconds=60),
            "per_tenant": RateLimitConfig(max_requests=100_000, window_seconds=3600),
        }
        self._windows: dict[str, SlidingWindow] = {}

    def configure(self, key: str, max_requests: int, window_seconds: int) -> None:
        """动态调整速率限制配置。"""
        self._limits[key] = RateLimitConfig(max_requests, window_seconds)
        self._windows.pop(key, None)

    async def check(
        self,
        user_id: str = "",
        agent_name: str = "",
        tenant_id: str = "",
    ) -> RateLimitResult:
        """检查是否超限。所有维度全部通过才算通过。

        Args:
            user_id: 用户 ID。
            agent_name: Agent 名称。
            tenant_id: 租户 ID。

        Returns:
            RateLimitResult。
        """
        checks = [
            (f"user:{user_id}", self._limits.get("per_user", RateLimitConfig())),
            (f"agent:{agent_name}", self._limits.get("per_agent", RateLimitConfig())),
            (f"tenant:{tenant_id}", self._limits.get("per_tenant", RateLimitConfig())),
        ]

        for key, config in checks:
            if not key or ":" not in key:
                continue
            if key not in self._windows:
                self._windows[key] = SlidingWindow(config.max_requests, config.window_seconds)

            allowed, retry_after = self._windows[key].allow()
            if not allowed:
                return RateLimitResult(
                    allowed=False,
                    retry_after=retry_after,
                    reason=f"速率限制 ({key}): 超过 {config.max_requests} 次/{config.window_seconds}s",
                )

        return RateLimitResult(allowed=True)

    def get_remaining(self, user_id: str = "", agent_name: str = "", tenant_id: str = "") -> dict[str, int]:
        """获取各维度的剩余请求数。"""
        result = {}
        for label, key in [("user", f"user:{user_id}"), ("agent", f"agent:{agent_name}"), ("tenant", f"tenant:{tenant_id}")]:
            window = self._windows.get(key)
            if window:
                result[label] = max(0, window.max_requests - len(window._timestamps))
            else:
                result[label] = -1  # 未初始化
        return result

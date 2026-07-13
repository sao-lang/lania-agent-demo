"""会话上下文风险策略：检测多步骤操作组合的风险模式。"""

from __future__ import annotations

from typing import Any

from app.harness.brain.models import SafetyContext, SafetyDecision
from app.harness.safety.engine import SafetyPolicy


class SessionContextPolicy(SafetyPolicy):
    """检测会话范围内的多步骤组合风险。

    单步都合法，但组合起来可能是攻击路径。
    例: 读取敏感文件 → 搜索外部 URL → 发送网络请求。
    """

    name = "session_context"
    description = "检测多步骤操作组合的风险模式"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        sc = cfg.get("session_context", {})

        self.risk_weights: dict[str, int] = sc.get("risk_weights", {
            "read_repository_file": 1,
            "search_repository": 1,
            "list_repository_files": 1,
            "calculator": 0,
            "web_search": 2,
            "web_fetch": 3,
            "rag_retrieve_evidence": 1,
            "shell_command": 5,
            "query_database": 4,
            "sandbox_exec": 3,
        })
        self.window_size: int = sc.get("window_size", 5)
        self.warn_threshold: int = sc.get("warn_threshold", 6)
        self.block_threshold: int = sc.get("block_threshold", 10)

    async def check(self, context: SafetyContext) -> SafetyDecision:
        history = context.session_history[-self.window_size:]
        total_score = sum(self.risk_weights.get(tool, 1) for tool in history)

        if total_score >= self.block_threshold:
            return SafetyDecision(
                allowed=False, level="block",
                reason=f"会话风险评分 {total_score} >= {self.block_threshold}（阻断阈值），"
                       f"最近 {len(history)} 步操作组合风险过高",
                details={
                    "score": total_score,
                    "threshold": self.block_threshold,
                    "recent_operations": history,
                    "category": "session_risk",
                },
            )

        if total_score >= self.warn_threshold:
            return SafetyDecision(
                allowed=True, level="warn",
                reason=f"会话风险评分 {total_score} >= {self.warn_threshold}（警告阈值），请关注操作组合",
                details={
                    "score": total_score,
                    "threshold": self.warn_threshold,
                    "recent_operations": history,
                    "category": "session_risk",
                },
            )

        return SafetyDecision(allowed=True, level="pass")

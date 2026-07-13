"""权限提升策略：检�?sudo/su/chown 等提权操作�?""

from __future__ import annotations

from typing import Any

from app.agent_platform.agents.brain.models import SafetyContext, SafetyDecision
from app.agent_platform.harness.safety.engine import SafetyPolicy


class PrivilegeEscalationPolicy(SafetyPolicy):
    """检测权限提升操作�?""

    name = "privilege_escalation"
    description = "检测权限提升操�?

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        dc = cfg.get("privilege_escalation", {})

        self.escalation_tools: list[str] = dc.get("escalation_tools", [
            "sudo", "su", "doas", "pkexec",
            "runas", "Start-Process -Verb RunAs",
            "docker exec", "kubectl exec",
            "chown", "chmod", "chgrp",
            "setfacl", "getfacl", "cacls", "icacls",
        ])
        self.permission_modes: list[str] = dc.get("permission_modes", [
            "777", "666", "7777", "+x", "+w", "+s", "u+s", "g+s", "o+w",
        ])

    async def check(self, context: SafetyContext) -> SafetyDecision:
        command = context.tool_args.get("command", "")
        if not command:
            return SafetyDecision(allowed=True, level="pass")

        norm = command.strip().lower()
        has_escalation = any(tool.lower() in norm for tool in self.escalation_tools)
        has_other_command = self._has_following_command(command)

        if has_escalation and has_other_command:
            return SafetyDecision(
                allowed=False, level="block",
                reason="检测到权限提升操作，需要用户明确确�?,
                details={"command": command, "category": "privilege_escalation"},
            )

        if any(mode in command for mode in self.permission_modes):
            return SafetyDecision(
                allowed=True, level="warn",
                reason="命令设置宽泛的文件权限，可能导致安全风险",
                details={"command": command, "category": "privilege_escalation"},
            )

        if has_escalation:
            return SafetyDecision(
                allowed=True, level="warn",
                reason="命令包含权限提升操作，建议确�?,
                details={"command": command, "category": "privilege_escalation"},
            )

        return SafetyDecision(allowed=True, level="pass")

    @staticmethod
    def _has_following_command(command: str) -> bool:
        parts = command.strip().split()
        for i, part in enumerate(parts):
            if part.lower() in {"sudo", "su", "doas", "runas", "pkexec"}:
                remaining = parts[i + 1:]
                non_flag = [p for p in remaining if not p.startswith("-")]
                return len(non_flag) > 0
        return False

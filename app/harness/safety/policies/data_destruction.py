"""数据破坏策略：检测不可逆的删除/覆盖操作。"""

from __future__ import annotations

from typing import Any

from app.harness.brain.models import SafetyContext, SafetyDecision
from app.harness.safety.engine import SafetyPolicy


class DataDestructionPolicy(SafetyPolicy):
    """检测不可逆的数据破坏操作。

    不枚举具体命令，用结构特征：
    - 递归删除（-r/-R/-rf 等标志）
    - 强制覆盖（-f/--force 标志）
    - 格式化/清零操作
    - 数据库 DROP/TRUNCATE/DELETE
    """

    name = "data_destruction"
    description = "检测不可逆的数据删除/覆盖操作"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        dc = cfg.get("data_destruction", {})

        self.recursive_flags: list[str] = dc.get("recursive_flags", [
            "-r", "-R", "--recursive", "-rf", "-Rf", "-fr",
            "-rfu", "--recursive --force",
        ])
        self.force_flags: list[str] = dc.get("force_flags", [
            "-f", "--force", "-y", "--yes", "--no-confirm",
        ])
        self.destruction_keywords: list[str] = dc.get("destruction_keywords", [
            "rm", "del", "delete", "remove", "erase",
            "rmdir", "rd", "format", "mkfs", "dd",
            "DROP", "TRUNCATE", "DELETE FROM",
            "clear", "clean", "purge", "shred", "wipe",
        ])

    async def check(self, context: SafetyContext) -> SafetyDecision:
        command = context.tool_args.get("command", "")
        if not command:
            return SafetyDecision(allowed=True, level="pass")

        norm = command.strip().lower()
        has_destruction = any(kw.lower() in norm for kw in self.destruction_keywords)
        has_recursive = any(flag in command for flag in self.recursive_flags)
        has_force = any(flag in command for flag in self.force_flags)
        has_batch = self._has_batch_scope(command)

        if has_destruction and has_recursive:
            return SafetyDecision(
                allowed=False, level="block",
                reason="递归删除操作可能造成不可逆的数据丢失",
                details={"command": command, "category": "data_destruction"},
            )

        if has_destruction and has_force and has_batch:
            return SafetyDecision(
                allowed=False, level="block",
                reason="强制批量删除操作可能造成不可逆的数据丢失",
                details={"command": command, "category": "data_destruction"},
            )

        if has_destruction:
            return SafetyDecision(
                allowed=True, level="warn",
                reason="命令包含数据删除操作，请确认影响范围",
                details={"command": command, "category": "data_destruction"},
            )

        return SafetyDecision(allowed=True, level="pass")

    @staticmethod
    def _has_batch_scope(command: str) -> bool:
        batch_indicators = [
            "*", "?", "[", "]", "..", "/", "\\", "~",
            "--all", "-a", "*.", "%",
        ]
        return any(ind in command for ind in batch_indicators)

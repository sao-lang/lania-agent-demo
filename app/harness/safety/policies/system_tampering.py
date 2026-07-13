"""系统篡改策略：检测系统级配置/文件篡改。"""

from __future__ import annotations

import re
from typing import Any

from app.harness.brain.models import SafetyContext, SafetyDecision
from app.harness.safety.engine import SafetyPolicy


class SystemTamperingPolicy(SafetyPolicy):
    """检测系统级配置篡改。"""

    name = "system_tampering"
    description = "检测系统级配置/文件篡改"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        dc = cfg.get("system_tampering", {})

        self.protected_paths: list[str] = dc.get("protected_paths", [
            "/etc/", "/boot/", "/lib/", "/usr/lib/", "/usr/bin/",
            "/usr/sbin/", "/sbin/", "/bin/", "/proc/", "/sys/",
            "/var/log/", "/var/spool/",
            "~/.ssh/", "~/.gnupg/",
            "/Library/",
            "C:\\Windows\\", "C:\\Program Files\\",
            "C:\\Program Files (x86)\\",
            "HKLM\\", "HKEY_LOCAL_MACHINE\\",
            "System32\\", "SysWOW64\\",
        ])
        self.system_tools: list[str] = dc.get("system_tools", [
            "systemctl", "service", "launchctl",
            "sc ", "sc.exe",
            "reg ", "regedit", "reg.exe",
            "sysctl", "modprobe", "insmod",
            "crontab", "at ", "schtasks",
            "hostname", "hostnamectl",
            "iptables", "nftables", "firewall-cmd", "ufw",
            "netsh", "wmic",
            "update-alternatives", "update-rc.d",
            "dpkg --configure", "rpm --",
        ])

    async def check(self, context: SafetyContext) -> SafetyDecision:
        command = context.tool_args.get("command", "")
        if not command:
            return SafetyDecision(allowed=True, level="pass")

        for path in self.protected_paths:
            if self._is_write_to_path(command, path):
                return SafetyDecision(
                    allowed=False, level="block",
                    reason=f"检测到对受保护系统路径的写入操作: {path}",
                    details={"command": command, "path": path, "category": "system_tampering"},
                )

        norm = command.strip().lower()
        has_system_tool = any(tool in norm for tool in self.system_tools)
        if has_system_tool and self._has_change_operation(command):
            return SafetyDecision(
                allowed=True, level="warn",
                reason="命令包含系统配置修改操作，可能影响系统稳定性",
                details={"command": command, "category": "system_tampering"},
            )

        return SafetyDecision(allowed=True, level="pass")

    @staticmethod
    def _is_write_to_path(command: str, path: str) -> bool:
        write_ops = r"(?:^|\s)(?:>|>>|tee|cp|mv|install|touch|mkdir|dd|write|save|export)"
        protected_pattern = re.escape(path)
        return bool(re.search(rf"{write_ops}.*{protected_pattern}", command, re.IGNORECASE))

    @staticmethod
    def _has_change_operation(command: str) -> bool:
        change_ops = [
            "enable", "disable", "start", "stop", "restart",
            "set", "add", "remove", "modify", "change",
            "install", "uninstall", "reload", "mask", "unmask",
        ]
        return any(op in command.lower() for op in change_ops)

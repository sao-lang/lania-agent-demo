"""数据外泄策略：检测向外部发送数据的操作。"""

from __future__ import annotations

import re
from typing import Any

from app.harness.brain.models import SafetyContext, SafetyDecision
from app.harness.safety.engine import SafetyPolicy


class DataExfiltrationPolicy(SafetyPolicy):
    """检测数据外泄操作。

    特征：网络请求 + 文件读取的组合、管道输出到网络、邮件发送附件、云存储上传。
    """

    name = "data_exfiltration"
    description = "检测向外部发送数据的操作"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        dc = cfg.get("data_exfiltration", {})

        self.exfil_tools: list[str] = dc.get("exfil_tools", [
            "curl", "wget", "nc", "netcat", "ncat", "socat", "telnet",
            "scp", "sftp", "rsync",
            "aws s3 cp", "gcloud storage cp", "azcopy",
            "mail", "sendmail", "mutt",
        ])
        self.pipe_to_network_patterns: list[str] = dc.get("pipe_to_network_patterns", [
            r"\|\s*(curl|wget|nc|netcat|socat)",
            r"\|\s*(bash|sh|zsh)\s+.*>(/dev/tcp|/dev/udp)",
        ])
        self.sensitive_extensions: list[str] = dc.get("sensitive_extensions", [
            ".env", ".pem", ".key", ".crt", ".cer",
            ".p12", ".pfx", ".jks", ".keystore",
            ".secret", ".credentials", ".config",
            ".sql", ".sqlite", ".db", ".log",
        ])

    async def check(self, context: SafetyContext) -> SafetyDecision:
        command = context.tool_args.get("command", "")
        if not command:
            return SafetyDecision(allowed=True, level="pass")

        norm = command.strip().lower()
        has_sensitive_file = any(ext in norm for ext in self.sensitive_extensions)
        has_exfil_tool = any(tool in norm for tool in self.exfil_tools)
        has_pipe_to_network = any(
            re.search(pattern, command, re.IGNORECASE)
            for pattern in self.pipe_to_network_patterns
        )

        if has_sensitive_file and has_exfil_tool:
            return SafetyDecision(
                allowed=False, level="block",
                reason="检测到敏感文件 + 网络发送工具，可能存在数据外泄风险",
                details={"command": command, "category": "data_exfiltration"},
            )

        if has_pipe_to_network:
            return SafetyDecision(
                allowed=False, level="block",
                reason="检测到管道输出到网络，可能存在数据外泄风险",
                details={"command": command, "category": "data_exfiltration"},
            )

        if has_exfil_tool:
            return SafetyDecision(
                allowed=True, level="warn",
                reason="命令包含网络发送工具，请确认不会发送敏感数据",
                details={"command": command, "category": "data_exfiltration"},
            )

        return SafetyDecision(allowed=True, level="pass")

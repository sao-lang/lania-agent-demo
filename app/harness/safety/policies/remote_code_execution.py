"""远程代码执行策略：检测"下载 + 执行"的结构模式。"""

from __future__ import annotations

import re
from typing import Any

from app.harness.brain.models import SafetyContext, SafetyDecision
from app.harness.safety.engine import SafetyPolicy


class RemoteCodeExecutionPolicy(SafetyPolicy):
    """检测远程代码执行模式。

    不枚举 curl/wget/bash 等具体命令，而是检测"下载 + 执行"的结构模式：
    - 任何下载工具 + 管道到解释器
    - 任何下载工具 + 保存到文件 + 执行该文件
    - eval/exec + 外部输入
    """

    name = "remote_code_execution"
    description = "检测下载并执行远程代码的模式"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        _ = config or {}
        self.download_tools: list[str] = [
            "curl", "wget", "fetch", "aria2", "axel",
            "python -c", "python3 -c",
            "Invoke-WebRequest", "iwr", "Start-BitsTransfer",
        ]
        self.interpreters: list[str] = [
            "bash", "sh", "zsh", "dash", "fish",
            "python", "python3", "perl", "ruby", "php",
            "node", "deno", "bun",
            "powershell", "pwsh", "cmd", "wscript", "cscript",
        ]
        self.pipe_to_interpreter: re.Pattern = re.compile(r"\|\s*(\w+)\s*$")
        self.download_then_exec: re.Pattern = re.compile(
            r"(curl|wget|fetch).*-[oO]\s+(\S+).*(&&|;|\n).*(\.\/\2|bash\s+\2|sh\s+\2|python\s+\2)",
            re.IGNORECASE,
        )

    async def check(self, context: SafetyContext) -> SafetyDecision:
        command = context.tool_args.get("command", "")
        if not command:
            return SafetyDecision(allowed=True, level="pass")

        # 检测 1: 管道到解释器模式
        pipe_match = self.pipe_to_interpreter.search(command)
        if pipe_match:
            receiver = pipe_match.group(1).lower()
            if receiver in self.interpreters:
                has_download = any(dt in command.lower() for dt in self.download_tools)
                if has_download:
                    return SafetyDecision(
                        allowed=False, level="block",
                        reason=f"检测到下载并直接管道执行模式 (→ {receiver})，存在远程代码执行风险",
                        details={"command": command, "interpreter": receiver,
                                 "category": "remote_code_execution"},
                    )

        # 检测 2: 下载到文件 + 执行文件模式
        if self.download_then_exec.search(command):
            return SafetyDecision(
                allowed=False, level="block",
                reason="检测到下载并执行远程文件模式，存在远程代码执行风险",
                details={"command": command, "category": "remote_code_execution"},
            )

        # 检测 3: eval/exec + 外部输入
        if self._has_eval_with_external_input(command):
            return SafetyDecision(
                allowed=False, level="block",
                reason="检测到 eval/exec 配合外部输入，存在代码注入风险",
                details={"command": command, "category": "remote_code_execution"},
            )

        return SafetyDecision(allowed=True, level="pass")

    @staticmethod
    def _has_eval_with_external_input(command: str) -> bool:
        eval_patterns = [
            r"\beval\s+", r"\bexec\s+", r"\bexec\(\s*\)",
            r"\.InvokeExpression\b",
            r"\bGet-Content\b.*\|.*Invoke-Expression\b",
        ]
        for pattern in eval_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

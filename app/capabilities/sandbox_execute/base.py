"""Sandbox Execute Capability 基础定义。

定义沙盒命令执行的 Protocol、数据模型和三级安全策略工厂。
从 command_tools.py 中提取的安全策略常量和模型，升级为独立的 Capability 层抽象。
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, Field


# ── 安全策略 ──────────────────────────────────

class CommandSecurityPolicy(BaseModel):
    """命令执行安全策略。

    支持三级预设策略（sandboxed / restricted / standard），
    也可通过 PolicyEngine 在运行时动态加载自定义策略。
    """

    allowed_commands: list[str] = Field(default_factory=list)
    blocked_commands: list[str] = Field(default_factory=list)
    blocked_patterns: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    max_output_bytes: int = 1_000_000
    max_command_length: int = 1000
    timeout_seconds_max: int = 300
    enable_network: bool = False
    enable_filesystem_write: bool = False
    writable_paths: list[str] = Field(default_factory=list)


# ── 请求 / 响应 ──────────────────────────────

class CommandExecutionRequest(BaseModel):
    """命令执行请求。"""

    command: str
    args: list[str] = Field(default_factory=list)
    working_directory: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    env_overrides: dict[str, str] = Field(default_factory=dict)


class CommandExecutionResult(BaseModel):
    """命令执行结果。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    truncated: bool = False
    duration_ms: int = 0


# ── Protocol ──────────────────────────────────

class SandboxExecuteCapability(Protocol):
    """沙盒命令执行能力协议。

    所有沙盒实现（本地子进程、Docker、远程沙盒）均遵循此协议。
    """

    def execute(
        self,
        request: CommandExecutionRequest,
        policy: CommandSecurityPolicy,
    ) -> CommandExecutionResult:
        """在安全策略约束下执行命令。"""
        ...


# ── 三级策略工厂 ──────────────────────────────

def build_sandboxed_policy() -> CommandSecurityPolicy:
    """最严格策略：只读文件，禁止网络，禁止写入。

    适用于：LLM 生成代码的静态检查、lint 工具运行。
    """
    return CommandSecurityPolicy(
        allowed_commands=sorted({
            "python", "python3", "pip", "pip3",
            "node", "npm", "npx",
            "git", "ls", "cat", "head", "tail", "grep",
            "find", "wc", "sort", "uniq", "echo", "pwd",
            "date", "which", "file", "du", "df",
        }),
        blocked_commands=sorted({
            "sudo", "su", "passwd", "chown",
            "kill", "pkill", "poweroff", "shutdown", "reboot",
            "dd", "mkfs", "fdisk", "mount", "umount",
            "telnet", "ssh", "scp", "sftp",
            "nc", "ncat",
            "curl", "wget",  # 禁止网络
        }),
        blocked_patterns=[
            r"rm\s+-rf\s+/",
            r"chmod\s+777",
            r">\s*/dev/",
            r"\|\s*sh\b",
            r"\|\s*bash\b",
            r"curl\s+.*\|\s*(sh|bash)",
            r"wget\s+.*\|\s*(sh|bash)",
        ],
        enable_network=False,
        enable_filesystem_write=False,
    )


def build_restricted_policy() -> CommandSecurityPolicy:
    """中等策略：允许 pip/npm 安装，允许写入 /tmp。

    适用于：需要安装依赖的代码分析任务。
    """
    return CommandSecurityPolicy(
        allowed_commands=sorted({
            "python", "python3", "pip", "pip3",
            "node", "npm", "npx",
            "git", "ls", "cat", "head", "tail", "grep",
            "find", "wc", "sort", "uniq", "echo", "pwd",
            "mkdir", "cp", "mv", "rm", "chmod",
            "date", "which", "file", "du", "df",
        }),
        blocked_commands=sorted({
            "sudo", "su", "passwd", "chown",
            "kill", "pkill", "poweroff", "shutdown", "reboot",
            "dd", "mkfs", "fdisk", "mount", "umount",
            "telnet", "ssh", "scp", "sftp",
            "nc", "ncat",
            "curl", "wget",  # 禁止网络
        }),
        blocked_patterns=[
            r"rm\s+-rf\s+/",
            r"chmod\s+777",
            r">\s*/dev/",
            r"\|\s*sh\b",
            r"\|\s*bash\b",
            r"curl\s+.*\|\s*(sh|bash)",
            r"wget\s+.*\|\s*(sh|bash)",
        ],
        enable_network=False,
        enable_filesystem_write=True,
        writable_paths=["/tmp"],
    )


def build_standard_policy() -> CommandSecurityPolicy:
    """标准策略：允许网络，允许文件写入，但限制危险命令。

    适用于：需要下载依赖或访问 API 的代码分析任务。
    """
    return CommandSecurityPolicy(
        allowed_commands=sorted({
            "python", "python3", "pip", "pip3",
            "node", "npm", "npx",
            "git", "ls", "cat", "head", "tail", "grep",
            "find", "wc", "sort", "uniq", "echo", "pwd",
            "mkdir", "cp", "mv", "rm", "chmod",
            "date", "which", "file", "du", "df",
            "curl", "wget",
        }),
        blocked_commands=sorted({
            "sudo", "su", "passwd", "chown",
            "kill", "pkill", "poweroff", "shutdown", "reboot",
            "dd", "mkfs", "fdisk", "mount", "umount",
            "telnet", "ssh", "scp", "sftp",
            "nc", "ncat",
        }),
        blocked_patterns=[
            r"rm\s+-rf\s+/",
            r"chmod\s+777",
            r">\s*/dev/",
            r"\|\s*sh\b",
            r"\|\s*bash\b",
            r"curl\s+.*\|\s*(sh|bash)",
            r"wget\s+.*\|\s*(sh|bash)",
        ],
        enable_network=True,
        enable_filesystem_write=True,
        writable_paths=["/tmp"],
    )
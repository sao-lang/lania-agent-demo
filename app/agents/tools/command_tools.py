"""Command 工具族定义——作为 Tool 的特化，不成为平行 runtime。

Command 不是独立顶层能力，而是 tool 的一种特化形式。统一进入 ToolRegistry
和 ToolExecutor，复用已有 policy、sandbox、audit 体系。

实现了 Shell 命令执行和仓库命令执行的本地子进程沙盒。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.base import (
    AgentTool, ToolExecutionError, ToolRetryPolicy,
)


# ── 安全策略 ──────────────────────────────────

DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "python", "python3", "pip", "pip3",
    "node", "npm", "npx",
    "git", "ls", "cat", "head", "tail", "grep",
    "find", "wc", "sort", "uniq", "echo", "pwd",
    "mkdir", "cp", "mv", "rm", "chmod",
    "date", "which", "file", "du", "df",
    "curl", "wget",
})

DEFAULT_BLOCKED_COMMANDS: frozenset[str] = frozenset({
    "sudo", "su", "passwd", "chown",
    "kill", "pkill", "poweroff", "shutdown", "reboot",
    "dd", "mkfs", "fdisk", "mount", "umount",
    "telnet", "ssh", "scp", "sftp",
    "nc", "ncat",
})

DEFAULT_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r'rm\s+-rf\s+/'),
    re.compile(r'chmod\s+777'),
    re.compile(r'>\s*/dev/'),
    re.compile(r'\|\s*sh\b'),
    re.compile(r'\|\s*bash\b'),
    re.compile(r'curl\s+.*\|\s*(sh|bash)'),
    re.compile(r'wget\s+.*\|\s*(sh|bash)'),
]


class CommandSecurityPolicy(BaseModel):
    """命令执行安全策略。"""

    allowed_commands: list[str] = Field(
        default_factory=lambda: sorted(DEFAULT_ALLOWED_COMMANDS),
    )
    blocked_commands: list[str] = Field(
        default_factory=lambda: sorted(DEFAULT_BLOCKED_COMMANDS),
    )
    allowed_paths: list[str] = Field(default_factory=list)
    max_output_bytes: int = 1_000_000
    max_command_length: int = 1000
    timeout_seconds_max: int = 300


# ── 输入 / 输出 ──────────────────────────────

class CommandInput(BaseModel):
    """命令执行所需的通用输入模型。"""

    command: str
    args: list[str] = Field(default_factory=list)
    working_directory: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class CommandOutput(BaseModel):
    """命令执行结果的通用模型。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    truncated: bool = False


# ── 安全校验函数 ─────────────────────────────

def validate_command(
    command: str,
    policy: CommandSecurityPolicy | None = None,
) -> None:
    """校验命令是否允许执行。"""
    if policy is None:
        policy = CommandSecurityPolicy()

    if len(command) > policy.max_command_length:
        raise ToolExecutionError(
            code="command_too_long",
            message=(
                f"Command too long ({len(command)}"
                f" > {policy.max_command_length})"
            ),
            error_type="validation_error",
            default_action="abort",
        )

    cmd_name = command.split()[0] if command else ""

    allowed = set(policy.allowed_commands)
    if cmd_name and allowed and cmd_name not in allowed:
        raise ToolExecutionError(
            code="command_not_allowed",
            message=f"Command '{cmd_name}' not in allowed list",
            error_type="permission_error",
            default_action="abort",
        )

    blocked = set(policy.blocked_commands)
    if cmd_name and blocked and cmd_name in blocked:
        raise ToolExecutionError(
            code="command_blocked",
            message=f"Command '{cmd_name}' is blocked",
            error_type="permission_error",
            default_action="abort",
        )

    for pattern in DEFAULT_BLOCKED_PATTERNS:
        if pattern.search(command):
            raise ToolExecutionError(
                code="command_pattern_blocked",
                message=f"Command blocked by pattern: {pattern.pattern}",
                error_type="permission_error",
                default_action="abort",
            )


def validate_working_directory(path: str | None) -> str | None:
    """校验工作目录是否安全。"""
    if path is None:
        return None
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise ToolExecutionError(
            code="directory_not_found",
            message=f"Directory not found: {path}",
            error_type="validation_error",
            default_action="abort",
        )
    if not resolved.is_dir():
        raise ToolExecutionError(
            code="not_a_directory",
            message=f"Not a directory: {path}",
            error_type="validation_error",
            default_action="abort",
        )
    return str(resolved)


# ── 执行函数 ──────────────────────────────────

def execute_command(
    command: str,
    args: list[str] | None = None,
    working_directory: str | None = None,
    timeout_seconds: int = 30,
    max_output_bytes: int = 1_000_000,
) -> CommandOutput:
    """在子进程中执行命令。

    Args:
        command: 命令名称。
        args: 命令参数列表。
        working_directory: 工作目录。
        timeout_seconds: 超时秒数。
        max_output_bytes: 输出截断字节数。

    Returns:
        命令执行结果。
    """
    validate_command(command)

    cmd_args = [command]
    if args:
        cmd_args.extend(args)

    cwd = validate_working_directory(working_directory)

    try:
        proc = subprocess.run(
            cmd_args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        stdout = proc.stdout[:max_output_bytes]
        stderr = proc.stderr[:max_output_bytes]
        truncated = (
            len(proc.stdout) > max_output_bytes
            or len(proc.stderr) > max_output_bytes
        )

        return CommandOutput(
            stdout=stdout, stderr=stderr,
            exit_code=proc.returncode,
            truncated=truncated,
        )

    except subprocess.TimeoutExpired:
        return CommandOutput(
            stderr=f"Timed out after {timeout_seconds}s",
            exit_code=-1,
        )
    except FileNotFoundError:
        return CommandOutput(
            stderr=f"Command not found: {command}",
            exit_code=-1,
        )
    except Exception as e:
        return CommandOutput(
            stderr=f"Execution error: {e}",
            exit_code=-1,
        )


# ── 工具类 ──────────────────────────────────

class BaseCommandTool(AgentTool):
    """命令工具基类。"""

    name = ""
    version = "v1"
    risk_level = "high"
    sandbox_mode = "process_isolated"
    timeout_ms = 30000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=500)
    trace_fields = [
        "tool_call_id", "task_id", "step_name", "tool_name",
        "duration_ms", "status", "exit_code", "truncated",
    ]
    input_model = CommandInput
    output_model = CommandOutput

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        raise NotImplementedError


class ShellCommandTool(BaseCommandTool):
    """Shell 命令执行工具。"""

    name = "shell_command"
    description = "在沙盒子进程中执行系统命令"

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        return execute_command(
            command=payload.command,
            args=payload.args,
            working_directory=payload.working_directory,
            timeout_seconds=payload.timeout_seconds,
        )


class RepositoryCommandTool(BaseCommandTool):
    """仓库操作工具。"""

    name = "repository_command"
    description = "在工作区目录下执行仓库操作（git/repo）"

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        repo_root = None
        if hasattr(context, "repository") and context.repository is not None:
            try:
                repo_root = getattr(context.repository, "root_path", None)
            except Exception:
                pass
        wd = payload.working_directory or repo_root
        return execute_command(
            command=payload.command,
            args=payload.args,
            working_directory=wd,
            timeout_seconds=payload.timeout_seconds,
        )

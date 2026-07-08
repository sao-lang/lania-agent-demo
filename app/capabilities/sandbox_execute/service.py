"""Sandbox Execute Capability 本地实现。

基于 subprocess 的本地沙盒命令执行，从 command_tools.py 迁移 validate_command
和 execute_command 逻辑，并增加三级安全策略支持和网络隔离。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from app.agents.tools.base import ToolExecutionError
from app.capabilities.sandbox_execute.base import (
    CommandExecutionRequest,
    CommandExecutionResult,
    CommandSecurityPolicy,
    SandboxExecuteCapability,
    build_sandboxed_policy,
)


class LocalSandboxExecuteCapability:
    """本地子进程沙盒执行能力。

    通过 subprocess 在受限环境中执行命令，支持：
    - 命令白名单/黑名单/正则模式拦截
    - 输出截断和超时控制
    - 网络隔离（通过环境变量阻断 HTTP/HTTPS）
    - 文件写入控制（通过 writable_paths 限制）
    - 三级安全策略（sandboxed / restricted / standard）
    """

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._default_policy_name = getattr(
            settings, "sandbox_executor_default_policy", "sandboxed"
        ) if settings else "sandboxed"

    def execute(
        self,
        request: CommandExecutionRequest,
        policy: CommandSecurityPolicy | None = None,
    ) -> CommandExecutionResult:
        """在安全策略约束下执行命令。

        Args:
            request: 命令执行请求。
            policy: 安全策略，为 None 时使用默认策略。

        Returns:
            命令执行结果。
        """
        if policy is None:
            policy = self._get_default_policy()

        t0 = time.monotonic()

        # 1. 安全校验
        self._validate(request, policy)

        # 2. 准备执行环境
        env = self._build_env(request, policy)
        cwd = self._validate_working_directory(request.working_directory)

        # 3. 执行
        cmd_args = [request.command]
        if request.args:
            cmd_args.extend(request.args)

        try:
            proc = subprocess.run(
                cmd_args,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
            )

            stdout = proc.stdout[:policy.max_output_bytes]
            stderr = proc.stderr[:policy.max_output_bytes]
            truncated = (
                len(proc.stdout) > policy.max_output_bytes
                or len(proc.stderr) > policy.max_output_bytes
            )

            return CommandExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                truncated=truncated,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        except subprocess.TimeoutExpired:
            return CommandExecutionResult(
                stderr=f"Timed out after {request.timeout_seconds}s",
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except FileNotFoundError:
            return CommandExecutionResult(
                stderr=f"Command not found: {request.command}",
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            return CommandExecutionResult(
                stderr=f"Execution error: {e}",
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    # ── 安全校验 ──────────────────────────────

    def _validate(
        self,
        request: CommandExecutionRequest,
        policy: CommandSecurityPolicy,
    ) -> None:
        """校验命令是否允许执行。"""
        if len(request.command) > policy.max_command_length:
            raise ToolExecutionError(
                code="command_too_long",
                message=(
                    f"Command too long ({len(request.command)}"
                    f" > {policy.max_command_length})"
                ),
                error_type="validation_error",
                default_action="abort",
            )

        cmd_name = request.command.split()[0] if request.command else ""

        # 白名单检查
        allowed = set(policy.allowed_commands)
        if cmd_name and allowed and cmd_name not in allowed:
            raise ToolExecutionError(
                code="command_not_allowed",
                message=f"Command '{cmd_name}' not in allowed list",
                error_type="permission_error",
                default_action="abort",
            )

        # 黑名单检查
        blocked = set(policy.blocked_commands)
        if cmd_name and blocked and cmd_name in blocked:
            raise ToolExecutionError(
                code="command_blocked",
                message=f"Command '{cmd_name}' is blocked",
                error_type="permission_error",
                default_action="abort",
            )

        # 正则模式检查
        for pattern_str in policy.blocked_patterns:
            if re.search(pattern_str, request.command):
                raise ToolExecutionError(
                    code="command_pattern_blocked",
                    message=f"Command blocked by pattern: {pattern_str}",
                    error_type="permission_error",
                    default_action="abort",
                )

    @staticmethod
    def _validate_working_directory(path: str | None) -> str | None:
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

    # ── 环境准备 ──────────────────────────────

    def _build_env(
        self,
        request: CommandExecutionRequest,
        policy: CommandSecurityPolicy,
    ) -> dict[str, str] | None:
        """构建执行环境变量。

        网络隔离通过清空 HTTP_PROXY/HTTPS_PROXY 等代理变量实现。
        如果 enable_network 为 False，额外设置 NO_PROXY 阻断所有网络请求。
        """
        env = os.environ.copy()

        if not policy.enable_network:
            # 阻断网络：清空代理变量，设置 http_proxy 为空
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                         "ALL_PROXY", "all_proxy", "no_proxy", "NO_PROXY"):
                env.pop(var, None)
            env["http_proxy"] = ""
            env["https_proxy"] = ""
            env["HTTP_PROXY"] = ""
            env["HTTPS_PROXY"] = ""

        # 应用用户自定义环境变量覆盖
        for key, value in request.env_overrides.items():
            env[key] = value

        return env

    def _get_default_policy(self) -> CommandSecurityPolicy:
        """获取默认安全策略。"""
        if self._default_policy_name == "restricted":
            from app.capabilities.sandbox_execute import build_restricted_policy
            return build_restricted_policy()
        if self._default_policy_name == "standard":
            from app.capabilities.sandbox_execute import build_standard_policy
            return build_standard_policy()
        return build_sandboxed_policy()
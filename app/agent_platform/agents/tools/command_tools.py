"""Command 工具族定义——作�?Tool 的特化，不成为平�?runtime�?
Command 不是独立顶层能力，而是 tool 的一种特化形式。统一进入 ToolRegistry
�?ToolExecutor，复用已�?policy、sandbox、audit 体系�?
安全校验和命令执行已抽取�?app.capabilities.sandbox_execute 模块�?本模块通过 context.services.get('sandbox_execute') 委托执行�?"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent_platform.agents.tools.base import (
    AgentTool, ToolExecutionError, ToolRetryPolicy,
)


# ── 输入 / 输出 ──────────────────────────────

class CommandInput(BaseModel):
    """命令执行所需的通用输入模型�?""

    command: str
    args: list[str] = Field(default_factory=list)
    working_directory: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class CommandOutput(BaseModel):
    """命令执行结果的通用模型�?""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    truncated: bool = False


# ── 工具�?──────────────────────────────────

class BaseCommandTool(AgentTool):
    """命令工具基类�?""

    name = ""
    version = "v1"
    risk_level = "high"
    execution_target = "client"
    sandbox_mode = "process_isolated"
    timeout_ms = 30000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=500)
    trace_fields = [
        "tool_call_id", "task_id", "step_name", "tool_name",
        "duration_ms", "status", "exit_code", "truncated",
    ]
    input_model = CommandInput
    output_model = CommandOutput

    def _get_sandbox(self, context: Any) -> Any:
        """�?context 中获�?SandboxExecuteCapability�?""
        services = getattr(context, "services", None) or {}
        sandbox = services.get("sandbox_execute")
        if sandbox is None:
            raise ToolExecutionError(
                code="sandbox_execute_unavailable",
                message="SandboxExecuteCapability is not configured",
                error_type="dependency_error",
                default_action="fallback",
            )
        return sandbox

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        raise NotImplementedError


class ShellCommandTool(BaseCommandTool):
    """Shell 命令执行工具�?""

    name = "shell_command"
    description = "在沙盒子进程中执行系统命�?

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        from app.agent_platform.capabilities.sandbox_execute import (
            CommandExecutionRequest,
            build_sandboxed_policy,
        )
        sandbox = self._get_sandbox(context)
        result = sandbox.execute(
            CommandExecutionRequest(
                command=payload.command,
                args=payload.args,
                working_directory=payload.working_directory,
                timeout_seconds=payload.timeout_seconds,
            ),
            policy=build_sandboxed_policy(),
        )
        return CommandOutput(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )


class RepositoryCommandTool(BaseCommandTool):
    """仓库操作工具�?""

    name = "repository_command"
    description = "在工作区目录下执行仓库操作（git/repo�?

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        from app.agent_platform.capabilities.sandbox_execute import (
            CommandExecutionRequest,
            build_sandboxed_policy,
        )
        repo_root = None
        if hasattr(context, "repository") and context.repository is not None:
            try:
                repo_root = getattr(context.repository, "root_path", None)
            except Exception:
                pass
        wd = payload.working_directory or repo_root

        sandbox = self._get_sandbox(context)
        result = sandbox.execute(
            CommandExecutionRequest(
                command=payload.command,
                args=payload.args,
                working_directory=wd,
                timeout_seconds=payload.timeout_seconds,
            ),
            policy=build_sandboxed_policy(),
        )
        return CommandOutput(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

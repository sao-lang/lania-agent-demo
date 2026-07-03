"""Command 工具族定义——作为 Tool 的特化，不成为平行 runtime。

Command 不是独立顶层能力，而是 tool 的一种特化形式。统一进入 ToolRegistry
和 ToolExecutor，复用已有 policy、sandbox、audit 体系。

当前模块提供占位定义，方便后续正式实现 shell/repo 操作时直接继承。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.base import AgentTool, ToolRetryPolicy


class CommandInput(BaseModel):
    """命令执行所需的通用输入模型。"""

    command: str
    args: list[str] = Field(default_factory=list)
    working_directory: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class CommandOutput(BaseModel):
    """命令执行结果的通用模型。"""

    stdout: str = ''
    stderr: str = ''
    exit_code: int = 0
    truncated: bool = False


class BaseCommandTool(AgentTool):
    """命令工具基类，子类应重写 run() 实现具体执行逻辑。

    统一元数据：
    - risk_level='high'（默认高风险）
    - sandbox_mode='process_isolated'（默认进程级隔离）
    - timeout_ms=30000（默认 30 秒超时）
    """

    name = ''
    version = 'v1'
    risk_level = 'high'
    sandbox_mode = 'process_isolated'
    timeout_ms = 30000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=500)
    trace_fields = [
        'tool_call_id', 'task_id', 'step_name', 'tool_name',
        'duration_ms', 'status', 'exit_code', 'truncated',
    ]
    input_model = CommandInput
    output_model = CommandOutput

    def run(self, payload: CommandInput, context: Any) -> CommandOutput:
        """由子类实现具体执行逻辑。"""
        raise NotImplementedError


class ShellCommandTool(BaseCommandTool):
    """Shell 命令执行工具。

    通过子进程执行系统命令（仅限白名单路径和命令）。
    """

    name = 'shell_command'
    description = '在沙盒子进程中执行系统命令'


class RepositoryCommandTool(BaseCommandTool):
    """仓库操作工具。

    在限定路径下执行 git/repo 操作。
    """

    name = 'repository_command'
    description = '在工作区目录下执行仓库操作（git/repo）'
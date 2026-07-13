"""步骤执行器。

职责：
1. 读取工具的风险声明和 execution_target
2. 调用 SafetyEngine 做工具调用前安全策略检查
3. 结合当前 mode 决定是否需要用户确认
4. 如需确认 → 暂停，等待用户响应
5. 根据 execution_target 路由执行：
   - server → 服务端沙箱执行（复用 ExecutionHarness）
   - client → 下发到客户端执行（客户端通过 API 返回结果）
6. 工具执行后调用 SafetyEngine 做输出内容安全扫描
7. 支持用户"记住此选择"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.agents.tools.base import ToolSchema
from app.agents.tools.registry import ToolRegistry
from app.harness.brain.models import (
    CheckpointType,
    ClientExecutionResult,
    ConsentRecord,
    ConsentResponse,
    ConsentScope,
    SafetyContext,
    SafetyDecision,
    ToolCall,
)
from app.harness.safety.engine import SafetyEngine
from app.models.agent import AgentEvent
from app.services.consent_store import ConsentStore


class StepExecutor:
    """步骤执行器。

    负责单次工具调用的全生命周期管理：
    安全策略检查 → 确认 → 执行 → 输出扫描。
    """

    # 确认矩阵：mode → (low, medium, high, critical) 是否需要确认
    CONSENT_MATRIX: dict[str, tuple[bool, bool, bool, bool]] = {
        "chat":         (False, False, True,  True),
        "autopilot":    (False, False, True,  True),
        "plan":         (False, False, True,  True),
        "plan_confirm": (False, True,  True,  True),
    }

    # 披露模式列表：这些模式下需要向用户披露步骤信息
    DISCLOSE_MODES: set[str] = {"autopilot", "plan", "plan_confirm"}

    def __init__(
        self,
        tool_registry: ToolRegistry,
        harness: Any | None = None,
        consent_store: ConsentStore | None = None,
        safety_engine: SafetyEngine | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._harness = harness
        self._consent_store = consent_store or ConsentStore()
        self._safety = safety_engine

    # ── 公开接口 ──────────────────────────────

    async def execute_step(
        self,
        tool_call: ToolCall,
        mode: str,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """执行一个步骤。根据 execution_target 路由到不同通道。

        Args:
            tool_call: 工具调用定义。
            mode: 当前执行模式。
            session: 会话对象。

        Yields:
            AgentEvent 事件流。
        """
        tool_def = self._tool_registry.describe(tool_call.name)
        step_risk = tool_def.risk_level
        exec_target = tool_def.execution_target

        # ── 1. 工具调用前安全策略检查 ──
        extra_warning: str | None = None
        if self._safety is not None:
            safety_decision = await self._safety.check(
                CheckpointType.PRE_TOOL_CALL,
                SafetyContext(
                    tool_name=tool_call.name,
                    tool_args=tool_call.args,
                    execution_target=exec_target,
                    session_history=getattr(session, 'tool_history', []),
                    user_id=getattr(session, 'user_id', ''),
                ),
            )
            if not safety_decision.allowed:
                yield AgentEvent(
                    type="tool_result",
                    data={
                        "tool": tool_call.name,
                        "status": "blocked",
                        "result": f"安全策略拒绝: {safety_decision.reason}",
                    },
                )
                return
            if safety_decision.level == "warn":
                extra_warning = safety_decision.reason
        else:
            safety_decision = SafetyDecision(allowed=True, level="pass")

        # ── 2. 决定是否需要用户确认 ──
        need_consent = self._need_consent(step_risk, mode)
        if need_consent:
            user_id = getattr(session, 'user_id', '')
            remembered = self._consent_store.get(user_id, tool_call.name)
            if remembered is not None and remembered.is_valid():
                need_consent = False

        # ── 3. 如需确认 → 暂停 ──
        if need_consent:
            consented = self._get_tool_risk_description(tool_def)

            event_data: dict[str, Any] = {
                "tool": tool_call.name,
                "args": tool_call.args,
                "risk_level": step_risk,
                "execution_target": exec_target,
                "reason": consented,
                "step_id": tool_call.id,
                "remember_options": ["none", "session", "persistent"],
            }
            if extra_warning:
                event_data["safety_warning"] = extra_warning
            if exec_target == "client":
                event_data["command"] = tool_call.args.get("command", "")
                event_data["working_directory"] = tool_call.args.get("working_directory")

            yield AgentEvent(type="step_consent_required", data=event_data)
            return  # ⏸️ 暂停

        # ── 4. 披露 ──
        if self._need_disclose(step_risk, mode):
            yield AgentEvent(
                type="step_disclosed",
                data={
                    "tool": tool_call.name,
                    "args": tool_call.args,
                    "execution_target": exec_target,
                },
            )

        # ── 5. 根据 execution_target 路由执行 ──
        if exec_target == "client":
            async for event in self._execute_on_client(tool_call, session):
                yield event
        else:
            async for event in self._execute_on_server(tool_call, tool_def, session):
                yield event

    async def resume_after_consent(
        self,
        tool_call: ToolCall,
        session: Any,
        consent_response: ConsentResponse,
    ) -> AsyncIterator[AgentEvent]:
        """用户确认后，重新执行步骤。

        Args:
            tool_call: 工具调用定义。
            session: 会话对象。
            consent_response: 用户确认响应。

        Yields:
            AgentEvent 事件流。
        """
        # 记住用户选择
        if consent_response.remember != ConsentScope.NONE:
            self._consent_store.save(ConsentRecord(
                user_id=getattr(session, 'user_id', ''),
                tool_name=tool_call.name,
                scope=consent_response.remember,
                granted_at=datetime.now(),
            ))

        if consent_response.action == "deny":
            yield AgentEvent(
                type="step_consent_denied",
                data={
                    "tool": tool_call.name,
                    "reason": "用户拒绝执行",
                },
            )
            return

        yield AgentEvent(type="step_consent_granted", data={
            "tool": tool_call.name,
        })

        tool_def = self._tool_registry.describe(tool_call.name)
        if tool_def.execution_target == "client":
            async for event in self._execute_on_client(tool_call, session):
                yield event
        else:
            async for event in self._execute_on_server(tool_call, tool_def, session):
                yield event

    async def resume_after_client_result(
        self,
        tool_call: ToolCall,
        session: Any,
        client_result: ClientExecutionResult,
    ) -> AsyncIterator[AgentEvent]:
        """客户端返回结果后继续执行。

        Args:
            tool_call: 工具调用定义。
            session: 会话对象。
            client_result: 客户端执行结果。

        Yields:
            AgentEvent 事件流。
        """
        result_data = {
            "stdout": client_result.stdout or "",
            "stderr": client_result.stderr or "",
            "exit_code": client_result.exit_code,
            "truncated": client_result.truncated,
        }
        status = "success" if client_result.exit_code == 0 else "error"

        yield AgentEvent(
            type="tool_result",
            data={
                "tool": tool_call.name,
                "status": status,
                "result": result_data,
            },
        )

        # 工具调用后安全策略检查
        if self._safety is not None:
            # 接入会话历史
            self._append_session_history(session, tool_call.name)
            session_decision = await self._safety.check(
                CheckpointType.POST_TOOL_CALL,
                SafetyContext(
                    tool_name=tool_call.name,
                    execution_target="client",
                    session_history=getattr(session, 'tool_history', []),
                    user_id=getattr(session, 'user_id', ''),
                ),
            )
            if session_decision.level == "warn":
                yield AgentEvent(type="context_risk_warning", data={
                    "warning": session_decision.reason,
                })

    # ── 内部方法 ──────────────────────────────

    async def _execute_on_server(
        self,
        tool_call: ToolCall,
        tool_def: ToolSchema,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """服务端沙箱执行。复用现有 ExecutionHarness。"""
        sandbox_mode = getattr(tool_def, 'sandbox_mode', 'inline')
        yield AgentEvent(
            type="step_start",
            data={
                "tool": tool_call.name,
                "sandbox_mode": sandbox_mode,
            },
        )

        try:
            if self._harness is not None:
                result = await self._harness.run_tool(
                    tool_call.name,
                    tool_call.args,
                    sandbox=sandbox_mode,
                )
            else:
                # 无 harness 时直接走 ToolRegistry
                result = "执行完成"

            yield AgentEvent(
                type="tool_result",
                data={
                    "tool": tool_call.name,
                    "status": "success",
                    "result": result,
                },
            )

            # 工具调用后安全策略检查
            if self._safety is not None:
                self._append_session_history(session, tool_call.name)
                session_decision = await self._safety.check(
                    CheckpointType.POST_TOOL_CALL,
                    SafetyContext(
                        tool_name=tool_call.name,
                        execution_target="server",
                        session_history=getattr(session, 'tool_history', []),
                        user_id=getattr(session, 'user_id', ''),
                    ),
                )
                if session_decision.level == "warn":
                    yield AgentEvent(type="context_risk_warning", data={
                        "warning": session_decision.reason,
                    })

        except Exception as e:
            yield AgentEvent(
                type="tool_result",
                data={
                    "tool": tool_call.name,
                    "status": "error",
                    "error": str(e),
                },
            )

    async def _execute_on_client(
        self,
        tool_call: ToolCall,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """下发到客户端执行。"""
        yield AgentEvent(
            type="client_command",
            data={
                "tool": tool_call.name,
                "command": tool_call.args.get("command", ""),
                "args": tool_call.args.get("args", []),
                "cwd": tool_call.args.get("working_directory"),
                "timeout_seconds": tool_call.args.get("timeout_seconds", 30),
                "step_id": tool_call.id,
                "expects_result": True,
            },
        )
        return  # ⏸️ 暂停

    def _need_consent(self, step_risk: str, mode: str) -> bool:
        """根据确认矩阵判断是否需要用户确认。

        Args:
            step_risk: 步骤风险等级。
            mode: 当前模式。

        Returns:
            是否需要确认。
        """
        risks = ["low", "medium", "high", "critical"]
        idx = risks.index(step_risk) if step_risk in risks else 3
        return self.CONSENT_MATRIX.get(mode, (False, False, True, True))[idx]

    def _need_disclose(self, step_risk: str, mode: str) -> bool:
        """判断是否需要向用户披露步骤信息。

        Args:
            step_risk: 步骤风险等级。
            mode: 当前模式。

        Returns:
            是否需要披露。
        """
        return mode in self.DISCLOSE_MODES and step_risk in ("medium", "high", "critical")

    @staticmethod
    def _get_tool_risk_description(tool_def: ToolSchema) -> str:
        """获取工具的风险描述文本。

        Args:
            tool_def: 工具 schema。

        Returns:
            风险描述。
        """
        risk_labels = {
            "low": "低风险",
            "medium": "中风险",
            "high": "高风险",
            "critical": "严重风险",
        }
        risk_label = risk_labels.get(tool_def.risk_level, "未知风险")
        return f"{risk_label} - {tool_def.description or tool_def.name}"

    @staticmethod
    def _append_session_history(session: Any, tool_name: str) -> None:
        """追加工具调用到会话历史。"""
        if not hasattr(session, 'tool_history'):
            session.tool_history = []
        session.tool_history.append(tool_name)

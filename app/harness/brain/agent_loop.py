"""LLM 驱动的工具调用循环。

核心循环：LLM 决定 → StepExecutor 执行（含安全策略、确认、客户端/服务端路由）→ 结果回传
支持多种暂停场景：
1. step_consent_required → 等待用户确认后 resume
2. client_command → 等待客户端返回结果后 resume
3. safety_blocked → 安全策略拒绝，终止当前步骤
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.harness.brain.intent_recognizer import IntentRecognizer
from app.harness.brain.mode_router import ModeRouter
from app.harness.brain.models import (
    ClientExecutionResult,
    ConsentResponse,
    IntentDecision,
    SuggestedMode,
    ToolCall,
)
from app.harness.brain.step_executor import StepExecutor
from app.models.agent import AgentEvent


@dataclass
class PauseState:
    """暂停状态，用于 resume 时恢复执行。"""
    messages: list[dict] = field(default_factory=list)
    paused_tc: ToolCall | None = None
    turn: int = 0
    pause_reason: str = ""
    decision: IntentDecision | None = None
    mode: str = ""
    available_tools: list[dict] = field(default_factory=list)


class AgentLoop:
    """LLM 驱动的工具调用循环。

    核心流程：
    1. 生成计划（如需规划）
    2. LLM 决定下一步调用的工具
    3. StepExecutor 执行（安全策略 → 确认 → 服务端/客户端）
    4. 结果回传给 LLM
    5. 重复直到 LLM 不再调用工具或达到最大轮次
    """

    MAX_TURNS = 8

    def __init__(
        self,
        llm: Any,
        step_executor: StepExecutor,
        intent_recognizer: IntentRecognizer | None = None,
        mode_router: ModeRouter | None = None,
        tool_registry: Any | None = None,
    ) -> None:
        self._llm = llm
        self._step_executor = step_executor
        self._intent_recognizer = intent_recognizer
        self._mode_router = mode_router or ModeRouter()
        self._tool_registry = tool_registry

        # 暂停状态存储: session_id → PauseState
        self._pause_states: dict[str, PauseState] = {}

    # ── 公开接口 ──────────────────────────────

    async def run(
        self,
        message: str,
        decision: IntentDecision,
        mode: str,
        history: list[dict],
        available_tools: list[dict],
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """启动工具调用循环。

        Args:
            message: 用户消息。
            decision: 意图识别结果。
            mode: 执行模式。
            history: 对话历史。
            available_tools: 可用工具列表（function calling schema）。
            session: 会话对象。

        Yields:
            AgentEvent 事件流。
        """
        # ── 1. 生成计划（如需规划） ──
        if decision.needs_planning or mode in (SuggestedMode.PLAN, SuggestedMode.PLAN_CONFIRM):
            plan = await self._generate_plan(message, decision, available_tools)
            if plan:
                yield AgentEvent(
                    type="plan",
                    data={
                        "steps": plan,
                        "summary": f"计划共 {len(plan)} 步",
                        "risk_level": decision.risk_level,
                    },
                )
                # 需要等待用户确认计划（plan_confirm 模式）
                if mode == SuggestedMode.PLAN_CONFIRM:
                    yield AgentEvent(
                        type="ask_user",
                        data={"question": "请确认是否执行上述计划？"},
                    )
                    return  # ⏸️ 暂停

        # ── 2. 构建消息列表 ──
        system_prompt = self._build_system_prompt(decision, mode)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-6:])  # 取最近 6 轮
        messages.append({"role": "user", "content": message})

        # ── 3. LLM 工具调用循环 ──
        for turn in range(self.MAX_TURNS):
            response = await self._llm.chat(messages, tools=available_tools)

            if not self._has_tool_calls(response):
                # ── 最终回答 ──
                content = self._extract_content(response)
                yield AgentEvent.delta(content)
                yield AgentEvent.completed()
                return

            for tc in self._extract_tool_calls(response):
                tool_call = ToolCall(
                    id=tc.get("id", ""),
                    name=tc["function"]["name"],
                    args=tc["function"].get("arguments", {}),
                )

                yield AgentEvent(
                    type="tool_call",
                    data={"tool": tool_call.name, "args": tool_call.args},
                )

                # 通过 StepExecutor 执行
                async for event in self._step_executor.execute_step(
                    tool_call=tool_call,
                    mode=mode,
                    session=session,
                ):
                    yield event

                    # 暂停处理
                    if event.type == "step_consent_required":
                        self._save_pause_state(
                            session, messages, tool_call, turn,
                            pause_reason="consent", decision=decision,
                            mode=mode, available_tools=available_tools,
                        )
                        return  # ⏸️ 暂停

                    if event.type == "client_command":
                        self._save_pause_state(
                            session, messages, tool_call, turn,
                            pause_reason="client_exec", decision=decision,
                            mode=mode, available_tools=available_tools,
                        )
                        return  # ⏸️ 暂停

                    if event.type == "tool_result":
                        result_content = self._format_result(event.data)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id or tc.get("id", ""),
                            "content": result_content,
                        })

        # 最大轮次限制
        yield AgentEvent.error("达到最大轮次限制")

    async def resume(
        self,
        session: Any,
        consent_response: ConsentResponse | None = None,
        client_result: ClientExecutionResult | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """恢复暂停的执行。

        Args:
            session: 会话对象。
            consent_response: 用户确认响应。
            client_result: 客户端执行结果。

        Yields:
            AgentEvent 事件流。
        """
        session_id = getattr(session, 'id', '')
        state = self._pause_states.pop(session_id, None)
        if state is None:
            yield AgentEvent.error("没有找到暂停状态")
            return

        if state.pause_reason == "consent" and consent_response:
            tool_call = state.paused_tc
            if tool_call is None:
                yield AgentEvent.error("暂停状态缺失工具调用信息")
                return

            if consent_response.action == "deny":
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "用户拒绝执行此步骤",
                })
            else:
                async for event in self._step_executor.resume_after_consent(
                    tool_call=tool_call,
                    session=session,
                    consent_response=consent_response,
                ):
                    if event.type == "client_command":
                        self._save_pause_state(
                            session, state.messages, tool_call, state.turn,
                            pause_reason="client_exec", decision=state.decision,
                            mode=state.mode, available_tools=state.available_tools,
                        )
                        yield event
                        return  # ⏸️ 再次暂停

                    if event.type == "tool_result":
                        result_content = self._format_result(event.data)
                        state.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_content,
                        })

                    yield event

        elif state.pause_reason == "client_exec" and client_result:
            tool_call = state.paused_tc
            if tool_call is None:
                yield AgentEvent.error("暂停状态缺失工具调用信息")
                return

            # 客户端执行结果不会立即传给 LLM，而是先记录
            async for event in self._step_executor.resume_after_client_result(
                tool_call=tool_call,
                session=session,
                client_result=client_result,
            ):
                if event.type == "tool_result":
                    result_content = self._format_result(event.data)
                    state.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_content,
                    })
                yield event

        # 继续 LLM 循环
        async for event in self._continue_loop(state, session):
            yield event

    # ── 内部方法 ──────────────────────────────

    async def _continue_loop(
        self,
        state: PauseState,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """继续 LLM 工具调用循环。"""
        messages = state.messages
        mode = state.mode
        available_tools = state.available_tools

        for turn in range(state.turn + 1, self.MAX_TURNS):
            response = await self._llm.chat(messages, tools=available_tools)

            if not self._has_tool_calls(response):
                content = self._extract_content(response)
                yield AgentEvent.delta(content)
                yield AgentEvent.completed()
                return

            for tc in self._extract_tool_calls(response):
                tool_call = ToolCall(
                    id=tc.get("id", ""),
                    name=tc["function"]["name"],
                    args=tc["function"].get("arguments", {}),
                )

                yield AgentEvent(
                    type="tool_call",
                    data={"tool": tool_call.name, "args": tool_call.args},
                )

                async for event in self._step_executor.execute_step(
                    tool_call=tool_call,
                    mode=mode,
                    session=session,
                ):
                    yield event

                    if event.type == "step_consent_required":
                        self._save_pause_state(
                            session, messages, tool_call, turn,
                            pause_reason="consent", decision=state.decision,
                            mode=mode, available_tools=available_tools,
                        )
                        return

                    if event.type == "client_command":
                        self._save_pause_state(
                            session, messages, tool_call, turn,
                            pause_reason="client_exec", decision=state.decision,
                            mode=mode, available_tools=available_tools,
                        )
                        return

                    if event.type == "tool_result":
                        result_content = self._format_result(event.data)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id or tc.get("id", ""),
                            "content": result_content,
                        })

        yield AgentEvent.error("达到最大轮次限制")

    def _save_pause_state(
        self,
        session: Any,
        messages: list[dict],
        tool_call: ToolCall,
        turn: int,
        pause_reason: str,
        decision: IntentDecision | None = None,
        mode: str = "",
        available_tools: list[dict] | None = None,
    ) -> None:
        """保存暂停状态。"""
        session_id = getattr(session, 'id', '')
        self._pause_states[session_id] = PauseState(
            messages=messages,
            paused_tc=tool_call,
            turn=turn,
            pause_reason=pause_reason,
            decision=decision,
            mode=mode,
            available_tools=available_tools or [],
        )

    async def _generate_plan(
        self,
        message: str,
        decision: IntentDecision,
        available_tools: list[dict],
    ) -> list[dict]:
        """生成执行计划。"""
        try:
            prompt = (
                "你是一个任务规划专家。分析用户请求并生成逐步执行计划。\n"
                f"用户请求: {message}\n"
                f"风险等级: {decision.risk_level.value}\n"
                f"建议来源: {[s.value for s in decision.suggested_sources]}\n\n"
                "请输出 JSON 格式的计划，每步包含 name、description、suggested_tool：\n"
                '[{"name": "步骤名称", "description": "步骤描述", "suggested_tool": "建议工具名"}]\n'
                "只输出 JSON 数组，不要输出额外解释。"
            )
            response = await self._llm.chat([{"role": "user", "content": prompt}])
            content = self._extract_content(response)
            import json
            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception:
            pass
        return []

    @staticmethod
    def _build_system_prompt(decision: IntentDecision, mode: str) -> str:
        """构建系统提示词。"""
        source_names = [s.value for s in decision.suggested_sources]
        return (
            "你是一个 AI 助手，可以使用工具来帮助用户。\n\n"
            f"当前模式: {mode}\n"
            f"建议的知识来源: {', '.join(source_names)}\n"
            f"风险等级: {decision.risk_level.value}\n"
            "在回答前，根据需要调用合适的工具获取信息。"
        )

    @staticmethod
    def _has_tool_calls(response: Any) -> bool:
        """检查 LLM 响应是否包含工具调用。"""
        if hasattr(response, 'tool_calls'):
            return bool(response.tool_calls)
        if hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and hasattr(choice.message, 'tool_calls'):
                return bool(choice.message.tool_calls)
            if hasattr(choice, 'finish_reason') and choice.finish_reason == 'tool_calls':
                return True
        return False

    @staticmethod
    def _extract_tool_calls(response: Any) -> list[dict]:
        """从 LLM 响应中提取工具调用列表。"""
        if hasattr(response, 'tool_calls'):
            return [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments if isinstance(tc.function.arguments, dict)
                        else __import__('json').loads(tc.function.arguments or '{}'),
                    },
                }
                for tc in (response.tool_calls or [])
            ]
        if hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and hasattr(choice.message, 'tool_calls'):
                return [
                    {
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                            if isinstance(tc.function.arguments, dict)
                            else __import__('json').loads(tc.function.arguments or '{}'),
                        },
                    }
                    for tc in (choice.message.tool_calls or [])
                ]
        return []

    @staticmethod
    def _extract_content(response: Any) -> str:
        """从 LLM 响应中提取文本内容。"""
        if hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                return choice.message.content or ""
            if hasattr(choice, 'text'):
                return choice.text or ""
        if hasattr(response, 'content'):
            return response.content or ""
        return str(response)

    @staticmethod
    def _format_result(event_data: dict) -> str:
        """格式化工具结果供 LLM 消费。"""
        result = event_data.get("result", "")
        if isinstance(result, dict):
            import json
            return json.dumps(result, ensure_ascii=False, default=str)
        return str(result)

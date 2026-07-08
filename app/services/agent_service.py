"""Agent 对话服务模块。

核心编排服务：接收用户输入 → 按 mode 决定流程 → 匹配 Capability → 执行。
是 Mode + Capability 模型的核心实现。
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator

from app.capabilities.registry import CapabilityRegistry
from app.capabilities.chat import ChatCapability
from app.capabilities.code_review import CodeReviewCapability
from app.capabilities.data_analysis import DataAnalysisCapability
from app.capabilities.web_search import WebSearchCapability
from app.capabilities.coding import CodingCapability
from app.models.agent import (
    AgentChatRequest,
    AgentCommandRequest,
    AgentCommandResponse,
    AgentEvent,
    IntentMatch,
    Plan,
)
from app.services.intent_matcher import IntentMatcher
from app.services.mcp_manager import McpManager
from app.services.plan_executor import PlanExecutor
from app.services.plan_generator import PlanGenerator
from app.services.session_manager import SessionManager, Message


class AgentService:
    """Agent 对话服务。

    职责:
    1. 解析 mode → 决定执行流程 (chat/plan/autopilot)
    2. 识别意图 → 匹配 Capability
    3. 路由到对应执行器
    4. 产生 SSE 事件流
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        intent_matcher: IntentMatcher,
        session_manager: SessionManager,
        mcp_manager: McpManager,
        plan_generator: PlanGenerator | None = None,
        plan_executor: PlanExecutor | None = None,
        task_orchestrator: Any | None = None,
        query_orchestrator: Any | None = None,
        llm: Any | None = None,
        tool_registry: Any | None = None,
        repository: Any | None = None,
        database: Any | None = None,
    ) -> None:
        self._registry = registry
        self._intent_matcher = intent_matcher
        self._session_manager = session_manager
        self._mcp_manager = mcp_manager
        self._plan_generator = plan_generator or PlanGenerator(registry, llm)
        self._plan_executor = plan_executor or PlanExecutor()
        self._task_orchestrator = task_orchestrator
        self._query_orchestrator = query_orchestrator
        self._llm = llm
        self._tool_registry = tool_registry
        self._repository = repository
        self._database = database

        # 注册 Capability 提供者
        self._registry.register_provider(ChatCapability(llm=llm))
        self._registry.register_provider(
            CodeReviewCapability(llm=llm),
        )
        self._registry.register_provider(
            DataAnalysisCapability(llm=llm),
        )
        self._registry.register_provider(
            WebSearchCapability(llm=llm),
        )
        self._registry.register_provider(
            CodingCapability(llm=llm),
        )

    # ── 公开接口 ──────────────────────────────

    async def process(
        self,
        request: AgentChatRequest,
    ) -> AsyncIterator[AgentEvent]:
        """处理 Agent 对话请求，产生事件流。

        根据 mode 决定执行流程:
        - chat: 直接执行
        - plan: 生成计划 → 等待确认 → 执行
        - autopilot: 自动执行 → 询问下一步
        """
        start_time = time.monotonic()

        # 1. 获取/创建会话
        session = await self._session_manager.get_or_create(request.session_id)
        if request.mode:
            session.mode = request.mode

        # 2. 处理 MCP 配置
        if request.mcp_config:
            mcp_tools = await self._mcp_manager.connect(request.mcp_config)
            for tool_def in mcp_tools:
                yield AgentEvent.tool_call(
                    tool=f"mcp:{tool_def.server}:{tool_def.name}",
                    args={"connected": True},
                )

        # 3. 识别 Capability
        capability = request.capability
        intent: IntentMatch | None = None

        if capability is None:
            intent = await self._intent_matcher.match(
                request.message,
                history=[m.model_dump() for m in session.history],
            )
            capability = intent.capability
            session.capability = capability
            yield AgentEvent.intent(capability, intent.confidence)
        else:
            yield AgentEvent.intent(capability, 1.0)

        # 4. 按 mode 执行
        if session.mode == "plan":
            async for event in self._handle_plan_mode(
                request, capability, session,
            ):
                yield event
        elif session.mode == "autopilot":
            async for event in self._handle_autopilot_mode(
                request, capability, session,
            ):
                yield event
        else:
            async for event in self._handle_chat_mode(
                request.message, capability, session,
            ):
                yield event

        # 5. 保存会话
        session.history.append(Message(role="user", content=request.message))
        await self._session_manager.save(session)

        # 完成事件
        duration_ms = int((time.monotonic() - start_time) * 1000)
        yield AgentEvent.completed(duration_ms=duration_ms)

    async def execute_command(
        self,
        request: AgentCommandRequest,
    ) -> AgentCommandResponse:
        """一次性命令执行（非流式）。"""
        start_time = time.monotonic()

        # 识别 Capability
        capability = request.capability
        if capability is None:
            intent = await self._intent_matcher.match(request.message)
            capability = intent.capability

        # 收集事件
        answer_parts: list[str] = []
        async for event in self._route_to_capability(
            request.message, capability, {},
        ):
            if event.type == "delta":
                answer_parts.append(event.data.get("content", ""))

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return AgentCommandResponse(
            answer="".join(answer_parts),
            capability=capability,
            duration_ms=duration_ms,
            mode=request.mode,
        )

    # ── 模式处理 ──────────────────────────────

    async def _handle_chat_mode(
        self,
        message: str,
        capability: str,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """chat 模式：直接执行。"""
        context = {
            "llm": self._llm,
            "history": [m.model_dump() for m in session.history],
            "collection_name": session.collection_name,
            "tool_registry": self._tool_registry,
            "repository": self._repository,
            "database": self._database,
        }
        async for event in self._route_to_capability(
            message, capability, context,
        ):
            yield event

    async def _handle_plan_mode(
        self,
        request: AgentChatRequest,
        capability: str,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """plan 模式：生成计划 → 等确认 → 执行。"""
        # 使用 PlanGenerator 生成计划
        context = {
            "message": request.message,
            "collection_name": request.collection_name,
            "tool_registry": self._tool_registry,
        }
        plan = await self._plan_generator.generate(
            request.message, capability, context,
        )

        # 将计划存入会话上下文，等待客户端确认
        session.context["current_plan"] = plan
        session.context["pending_capability"] = capability
        await self._session_manager.save(session)

        yield AgentEvent(type="plan", data={
            "steps": [s.model_dump() for s in plan.steps],
            "summary": plan.summary,
        })
        # 客户端收到 plan 事件后，通过 POST /agent/plan/confirm 确认或拒绝

    async def _handle_autopilot_mode(
        self,
        request: AgentChatRequest,
        capability: str,
        session: Any,
    ) -> AsyncIterator[AgentEvent]:
        """autopilot 模式：自动执行 → 询问下一步。"""
        context = {
            "message": request.message,
            "collection_name": request.collection_name,
            "llm": self._llm,
            "history": [m.model_dump() for m in session.history],
            "tool_registry": self._tool_registry,
        }

        # 生成计划并自动执行
        plan = await self._plan_generator.generate(
            request.message, capability, context,
        )

        yield AgentEvent(type="plan", data={
            "steps": [s.model_dump() for s in plan.steps],
            "summary": f"自动执行: {plan.summary}",
        })

        # 使用 PlanExecutor 自动执行
        async for event in self._plan_executor.execute(
            plan, capability, context,
        ):
            yield event

        # 完成后询问下一步
        yield AgentEvent.ask_user("任务已完成。还需要我做什么？")

    # ── 内部方法 ──────────────────────────────

    async def _route_to_capability(
        self,
        message: str,
        capability: str,
        context: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """路由到 Capability 对应的执行器。"""
        # 检查是否有 Provider
        provider = self._registry.get_provider(capability)
        if provider is not None:
            events = await provider.execute(message, context)
            for event in events:
                yield event
            return

        # 检查是否有 Workflow
        cap_def = self._registry.get(capability)
        if cap_def and cap_def.workflow_type:
            async for event in self._run_workflow(
                cap_def.workflow_type, message, context,
            ):
                yield event
            return

        # 兜底：通用对话
        chat_provider = self._registry.get_provider("chat")
        if chat_provider:
            events = await chat_provider.execute(message, context)
            for event in events:
                yield event

    async def _run_workflow(
        self,
        workflow_type: str,
        message: str,
        context: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """通过 TaskWorkflow 执行。"""
        if workflow_type == "document_analysis" and self._task_orchestrator:
            yield AgentEvent.step_start(1, "文档分析", "调用文档分析工作流")
            # 创建 Task 并执行
            try:
                # 通过已有 TaskService 创建任务
                # TODO: 集成 TaskService
                yield AgentEvent.delta(f"已创建文档分析任务，指令: {message[:50]}...")
                yield AgentEvent.step_end(1, "completed")
            except Exception as e:
                yield AgentEvent.error(f"工作流执行失败: {e}")
        else:
            yield AgentEvent.delta(f"工作流 '{workflow_type}' 尚未实现")

    async def _generate_plan(self, message: str, capability: str) -> Plan:
        """生成执行计划（委托给 PlanGenerator）。"""
        return await self._plan_generator.generate(message, capability)

    async def execute_plan(
        self,
        session_id: str,
        plan: Plan,
    ) -> AsyncIterator[AgentEvent]:
        """执行已确认的计划。"""
        session = await self._session_manager.get(session_id)
        if session is None:
            yield AgentEvent.error("会话不存在")
            return

        capability = session.context.get("pending_capability", "chat")
        context = {
            "message": session.history[-1].content if session.history else "",
            "collection_name": session.collection_name,
            "llm": self._llm,
            "tool_registry": self._tool_registry,
        }

        yield AgentEvent(type="plan_confirmed", data={})

        # 使用 PlanExecutor 按步骤执行
        async for event in self._plan_executor.execute(
            plan, capability, context,
        ):
            yield event

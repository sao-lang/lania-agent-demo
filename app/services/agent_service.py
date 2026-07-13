"""Agent 对话服务模块。

核心编排服务：接收用户输入 → 按 mode 决定流程 → 匹配 Capability → 执行。

Token 节省策略：
- 扩展清单：只给大模型一份名字+描述的菜单（~50 tokens/扩展）
- 按需加载：大模型调用 load_extension / load_rule 工具获取完整内容
- Prompt 由系统处理，不在清单中
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
from app.services.agent_def_manager import AgentDefManager
from app.services.extension_catalog import ExtensionCatalog
from app.services.intent_matcher import IntentMatcher
from app.services.mcp_manager import McpManager
from app.services.plan_executor import PlanExecutor
from app.services.plan_generator import PlanGenerator
from app.services.skill_manager import SkillManager
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
        skill_manager: SkillManager | None = None,
        agent_def_manager: AgentDefManager | None = None,
        catalog: ExtensionCatalog | None = None,
        customization_engine: Any | None = None,
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
        self._skill_manager = skill_manager
        self._agent_def_manager = agent_def_manager
        self._customization_engine = customization_engine
        self._catalog = catalog or ExtensionCatalog(
            skill_manager=skill_manager,
            agent_def_manager=agent_def_manager,
            mcp_manager=mcp_manager,
        )

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
        - auto: 自动判断任务复杂度，选择 chat 或 plan（默认）
        - chat: 直接执行
        - plan: 生成计划 → 等待确认 → 执行
        - autopilot: 自动执行 → 询问下一步
        """
        start_time = time.monotonic()

        # 1. 获取/创建会话
        session = await self._session_manager.get_or_create(request.session_id)
        if request.mode:
            session.mode = request.mode

        # 1a. 解析 Agent 身份（请求级 > 会话级 > 默认）
        agent_name = request.agent_name or request.agent_id
        if agent_name is None and session.agent_name:
            agent_name = session.agent_name

        # 写入会话（持久化 Agent 选择）
        if agent_name and agent_name != session.agent_name:
            await self._session_manager.set_agent_name(session.id, agent_name)

        # 1.5 构建系统提示词（扩展清单，轻量 ~50 tokens/扩展）
        system_prompt, allowed_tools = await self._build_system_prompt(
            agent_name=agent_name,
        )
        if system_prompt:
            session.context["system_prompt"] = system_prompt
        if allowed_tools is not None:
            session.context["allowed_tools"] = allowed_tools
        yield AgentEvent(type="system_prompt", data={
            "length": len(system_prompt),
            "agent_name": agent_name,
            "allowed_tools": allowed_tools or [],
        })

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

        # 4. 自动模式 → 根据任务复杂度决定实际执行模式
        resolved_mode = session.mode
        if resolved_mode == "auto":
            resolved_mode = await self._resolve_mode(request.message, capability)
            session.mode = resolved_mode

        # 5. 按 resolved_mode 执行
        if resolved_mode == "plan":
            async for event in self._handle_plan_mode(
                request, capability, session,
            ):
                yield event
        elif resolved_mode == "autopilot":
            async for event in self._handle_autopilot_mode(
                request, capability, session,
            ):
                yield event
        else:
            async for event in self._handle_chat_mode(
                request.message, capability, session,
            ):
                yield event

        # 6. 保存会话
        session.history.append(Message(role="user", content=request.message))
        await self._session_manager.save(session)

        # 7. 完成事件
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

    # ── 扩展清单 + 系统提示词 ──────────────────

    async def _build_system_prompt(
        self,
        agent_name: str | None = None,
    ) -> tuple[str, list[str] | None]:
        """构建系统提示词：Agent 指令 + 扩展清单 + 工具白名单。

        优先使用 CustomizationEngine 统一构建，
        回退到原有的 AgentDefManager + ExtensionCatalog 逻辑。

        Returns:
            (system_prompt, allowed_tools) 元组。
        """
        # 使用 CustomizationEngine（如果可用）
        if self._customization_engine:
            sc = await self._customization_engine.build_session_context(
                agent_name=agent_name,
            )
            parts: list[str] = []
            if sc.system_prompt:
                parts.append(sc.system_prompt)
            if sc.extension_catalog:
                parts.append(sc.extension_catalog)
            return "\n\n".join(parts) if parts else "", sc.allowed_tools

        # 回退到旧逻辑
        parts: list[str] = []
        agent = None
        if self._agent_def_manager:
            if agent_name:
                agent = await self._agent_def_manager.get_by_name(agent_name)
            if agent is None:
                agent = await self._agent_def_manager.get_default()
        if agent and agent.instructions:
            parts.append(agent.instructions)

        skill_names = agent.skills if agent else None
        catalog = await self._catalog.build_catalog(skill_names)
        if catalog:
            parts.append(catalog)

        return "\n\n".join(parts) if parts else "", agent.allowed_tools if agent else None

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
            "allowed_tools": session.context.get("allowed_tools"),
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

    async def _resolve_mode(self, message: str, capability: str) -> str:
        """根据任务复杂度自动选择执行模式。

        策略:
        1. 复杂能力需要多个工具 → 直接走 plan 模式
        2. LLM 判断是否需要规划（当有 LLM 可用时）
        3. 默认：简单消息 → chat，长消息 → plan

        Args:
            message: 用户问题
            capability: 识别出的 Capability

        Returns:
            解析后的实际执行模式 ("chat" 或 "plan")
        """
        # 规则 1: 根据能力类型决策
        # 已知需要多工具执行的能力走 plan
        NEED_PLAN_CAPABILITIES = {
            "code_review", "data_analysis", "document_analysis",
        }
        if capability in NEED_PLAN_CAPABILITIES:
            return "plan"

        # 规则 2: 根据能力定义中的工具数量决策
        cap_def = self._registry.get(capability)
        if cap_def and cap_def.tools and len(cap_def.tools) >= 2:
            return "plan"

        # 规则 3: 根据输入长度粗略判断复杂度（启发式）
        # 长消息更可能是复杂任务需要规划
        if len(message.strip()) > 50:
            return "plan"

        # 规则 4: 关键词触发规划
        NEED_PLAN_KEYWORDS = [
            "分步", "分步骤", "一步步", "分解", "一步步来",
            "分阶段", "计划一下", "先规划", "列出步骤",
        ]
        for kw in NEED_PLAN_KEYWORDS:
            if kw in message:
                return "plan"

        # 默认: 简单对话 → chat
        return "chat"

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

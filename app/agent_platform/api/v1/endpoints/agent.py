"""Agent 对话 API。

统一的 Agent 交互入口，替代原有的多端点 (query/chat/tasks)。
Mode + Capability 模型的外层表达。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.container import AppContainer
from app.models.agent import (
    AgentChatRequest,
    AgentCommandRequest,
    AgentCommandResponse,
    PlanConfirmRequest,
)

router = APIRouter(prefix="/agent", tags=["agent"])


def get_container() -> AppContainer:
    """获取容器实例（由 FastAPI 依赖注入）。"""
    from fastapi import Request

    async def _get(request: Request) -> AppContainer:
        return request.app.state.container

    return _get


@router.post("/chat")
async def agent_chat(
    request: AgentChatRequest,
    container: AppContainer = Depends(get_container()),
):
    """Agent 对话 - SSE 流式返回。

    唯一的 Agent 交互入口。
    mode=auto: 自动判断（默认）
    mode=chat: 直接执行
    mode=plan: 先出计划 → 等待确认 → 执行
    mode=autopilot: 自动执行 → 询问下一步
    """
    agent_service = container.agent_service

    async def event_generator():
        async for event in agent_service.process(request):
            yield {
                "event": event.type,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(event_generator())


@router.post("/command")
async def agent_command(
    request: AgentCommandRequest,
    container: AppContainer = Depends(get_container()),
) -> AgentCommandResponse:
    """一次性命令 - 同步返回（非流式）。"""
    agent_service = container.agent_service
    return await agent_service.execute_command(request)


@router.post("/plan/confirm")
async def confirm_plan(
    request: PlanConfirmRequest,
    container: AppContainer = Depends(get_container()),
):
    """确认/拒绝计划 (plan 模式)。"""
    if not request.confirmed:
        return {"status": "rejected", "message": "计划已拒绝"}

    agent_service = container.agent_service
    # 获取会话中的计划
    session_manager = container.session_manager
    session = await session_manager.get(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    plan = session.context.get("current_plan")
    if plan is None:
        raise HTTPException(status_code=400, detail="当前会话没有待确认的计划")

    async def event_generator():
        async for event in agent_service.execute_plan(
            request.session_id, plan,
        ):
            yield {
                "event": event.type,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(event_generator())


@router.put("/session/{session_id}/mode")
async def set_session_mode(
    session_id: str,
    mode: str,
    container: AppContainer = Depends(get_container()),
):
    """切换会话的执行模式。"""
    session_manager = container.session_manager
    await session_manager.set_mode(session_id, mode)
    return {"status": "ok", "session_id": session_id, "mode": mode}


@router.put("/session/{session_id}/agent")
async def set_session_agent(
    session_id: str,
    agent_name: str | None = None,
    container: AppContainer = Depends(get_container()),
):
    """切换会话使用的 Agent 定义。

    设置 agent_name 为 None 表示清除 Agent 选择（使用默认 Agent）。
    """
    session_manager = container.session_manager
    await session_manager.set_agent_name(session_id, agent_name)
    return {
        "status": "ok",
        "session_id": session_id,
        "agent_name": agent_name,
    }

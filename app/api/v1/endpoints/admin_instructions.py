"""系统指令管理 API。

管理 .lania/AGENTS.md 项目级系统指令的读取和更新。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import InstructionsResponse, InstructionsUpdateRequest

router = APIRouter(prefix="/admin/instructions", tags=["admin"])


def get_container():
    from fastapi import Request

    async def _get(request: Request) -> AppContainer:
        return request.app.state.container
    return _get


_INSTRUCTIONS_PATH = Path(".lania") / "AGENTS.md"


@router.get("", response_model=InstructionsResponse)
async def get_instructions(
    _: None = Depends(RequirePermission("admin.instructions")),
    container: AppContainer = Depends(get_container()),
) -> InstructionsResponse:
    """获取当前项目级系统指令。"""
    content = container.instructions_manager.load_project_instructions()
    return InstructionsResponse(
        content=content,
        length=len(content),
        source="file",
    )


@router.put("", response_model=InstructionsResponse)
async def update_instructions(
    request: InstructionsUpdateRequest,
    _: None = Depends(RequirePermission("admin.instructions")),
) -> InstructionsResponse:
    """更新项目级系统指令（直接写入 .lania/AGENTS.md）。"""
    _INSTRUCTIONS_PATH.write_text(request.content, encoding="utf-8")
    return InstructionsResponse(
        content=request.content,
        length=len(request.content),
        source="file",
    )


@router.post("/reset", response_model=InstructionsResponse)
async def reset_instructions(
    _: None = Depends(RequirePermission("admin.instructions")),
) -> InstructionsResponse:
    """清空系统指令。"""
    if _INSTRUCTIONS_PATH.exists():
        _INSTRUCTIONS_PATH.write_text("", encoding="utf-8")
    return InstructionsResponse(content="", length=0, source="file")

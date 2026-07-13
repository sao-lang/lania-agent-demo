"""文件指令管理 API。

管理 .lania/instructions/*.instructions.md 文件级指令的 CRUD。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.container import AppContainer
from app.core.auth import RequirePermission
from app.models.admin import FileInstructionCreate, FileInstructionResponse, FileInstructionUpdate
from app.services.file_instruction_manager import FileInstructionManager

router = APIRouter(prefix="/admin/file-instructions", tags=["admin"])

_INSTRUCTIONS_DIR = Path(".lania") / "instructions"


def get_container():
    from fastapi import Request

    async def _get(request: Request) -> AppContainer:
        return request.app.state.container
    return _get


def _file_path(name: str) -> Path:
    return _INSTRUCTIONS_DIR / f"{name}.instructions.md"


def _read_file_instruction(name: str) -> FileInstructionResponse | None:
    fpath = _file_path(name)
    if not fpath.exists():
        return None
    frontmatter, body = FileInstructionManager._parse_frontmatter(fpath.read_text(encoding="utf-8"))
    return FileInstructionResponse(
        name=name,
        apply_to=frontmatter.get("applyTo", "**/*"),
        content=body.strip(),
    )


@router.get("", response_model=list[FileInstructionResponse])
async def list_file_instructions(
    _: None = Depends(RequirePermission("admin.file_instructions")),
    container: AppContainer = Depends(get_container()),
) -> list[FileInstructionResponse]:
    """列出所有文件指令。"""
    container.file_instruction_manager.load_all()
    return [
        FileInstructionResponse(
            name=inst.name,
            apply_to=inst.apply_to,
            content=inst.content,
        )
        for inst in container.file_instruction_manager.instructions
    ]


@router.post("", response_model=FileInstructionResponse, status_code=201)
async def create_file_instruction(
    request: FileInstructionCreate,
    _: None = Depends(RequirePermission("admin.file_instructions")),
) -> FileInstructionResponse:
    """创建文件指令。"""
    fpath = _file_path(request.name)
    if fpath.exists():
        raise HTTPException(status_code=409, detail=f"File instruction '{request.name}' already exists")

    content = f"---\napplyTo: \"{request.apply_to}\"\nname: {request.name}\n---\n\n{request.content}"
    fpath.write_text(content, encoding="utf-8")
    return FileInstructionResponse(
        name=request.name,
        apply_to=request.apply_to,
        content=request.content,
    )


@router.get("/{name}", response_model=FileInstructionResponse)
async def get_file_instruction(
    name: str,
    _: None = Depends(RequirePermission("admin.file_instructions")),
) -> FileInstructionResponse:
    """获取单个文件指令。"""
    result = _read_file_instruction(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"File instruction '{name}' not found")
    return result


@router.put("/{name}", response_model=FileInstructionResponse)
async def update_file_instruction(
    name: str,
    request: FileInstructionUpdate,
    _: None = Depends(RequirePermission("admin.file_instructions")),
) -> FileInstructionResponse:
    """更新文件指令。"""
    fpath = _file_path(name)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"File instruction '{name}' not found")

    current = _read_file_instruction(name)
    if current is None:
        raise HTTPException(status_code=404, detail=f"File instruction '{name}' not found")

    apply_to = request.apply_to if request.apply_to is not None else current.apply_to
    content = request.content if request.content is not None else current.content

    new_content = f"---\napplyTo: \"{apply_to}\"\nname: {name}\n---\n\n{content}"
    fpath.write_text(new_content, encoding="utf-8")
    return FileInstructionResponse(name=name, apply_to=apply_to, content=content)


@router.delete("/{name}")
async def delete_file_instruction(
    name: str,
    _: None = Depends(RequirePermission("admin.file_instructions")),
) -> dict:
    """删除文件指令。"""
    fpath = _file_path(name)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"File instruction '{name}' not found")
    fpath.unlink()
    return {"status": "ok", "name": name}

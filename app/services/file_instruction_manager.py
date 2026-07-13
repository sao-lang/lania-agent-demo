"""文件级指令管理器。

从 .agents/instructions/*.instructions.md 加载文件指令，
按 applyTo glob 模式匹配目标文件路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileInstruction:
    """文件指令——特定文件的针对性约束。"""
    name: str
    apply_to: str           # glob 模式，如 "app/**/*.py"
    content: str


class FileInstructionManager:
    """文件指令管理器。

    扫描 .agents/instructions/ 目录下的所有 *.instructions.md 文件，
    解析 YAML frontmatter + body，支持按文件路径匹配。
    """

    def __init__(self, instructions_dir: str | Path | None = None) -> None:
        self._dir = Path(instructions_dir) if instructions_dir else Path(".lania/instructions")
        self._instructions: list[FileInstruction] = []

    def load_all(self) -> None:
        """扫描并加载所有 .instructions.md 文件。"""
        self._instructions = []
        if not self._dir.exists():
            return
        for fpath in sorted(self._dir.glob("*.instructions.md")):
            frontmatter, body = self._parse_frontmatter(fpath.read_text(encoding="utf-8"))
            self._instructions.append(FileInstruction(
                name=frontmatter.get("name", fpath.stem),
                apply_to=frontmatter.get("applyTo", "**/*"),
                content=body.strip(),
            ))

    def match(self, file_path: str) -> list[FileInstruction]:
        """返回匹配给定文件路径的所有指令。"""
        path_obj = Path(file_path)
        return [
            inst for inst in self._instructions
            if path_obj.match(inst.apply_to)
        ]

    @property
    def instructions(self) -> list[FileInstruction]:
        """返回当前加载的全部指令列表。"""
        return list(self._instructions)

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """解析 YAML frontmatter。"""
        from app.services.frontmatter_parser import FrontmatterParser
        result = FrontmatterParser.parse(content, validate=False)
        return result.raw, result.body

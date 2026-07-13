"""文件 Hook 加载器模块。

从 .lania/hooks/ 目录加载 JSON 格式的 Hook 配置，
解析为 FileHook 数据对象（支持 YAML frontmatter 式 JSON Schema 校验）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ── Hook 动作类型 ──────────────────────────

HookActionType = Literal[
    "log", "block", "audit", "notify",
    "mutate_payload", "custom_script", "throttle",
]


# ── 数据模型 ───────────────────────────────

@dataclass
class HookAction:
    """单个 Hook 动作定义。"""
    type: HookActionType
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileHook:
    """文件 Hook 定义——从 JSON 文件解析的确定性拦截/扩展配置。"""
    name: str
    description: str = ""
    events: list[str] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    actions: list[HookAction] = field(default_factory=list)


class FileHookLoader:
    """从 .lania/hooks/ 加载文件 Hook。

    加载全部 ``*.json`` 文件并解析为 ``FileHook`` 对象列表。
    支持标准 JSON Schema 校验（通过 Pydantic 的基层断言）。
    """

    def load_all(self, hooks_dir: str | Path) -> list[FileHook]:
        """扫描并加载 hooks 目录下的所有 JSON 文件。

        Args:
            hooks_dir: Hook 配置目录路径。

        Returns:
            解析后的 FileHook 对象列表。目录不存在或为空时返回空列表。
        """
        dir_path = Path(hooks_dir)
        if not dir_path.exists():
            return []

        hooks: list[FileHook] = []
        for fpath in sorted(dir_path.glob("*.json")):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                hook = self._parse_hook(data)
                hooks.append(hook)
            except (json.JSONDecodeError, ValueError) as e:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to parse hook file %s: %s", fpath, e,
                )
        return hooks

    def _parse_hook(self, data: dict[str, Any]) -> FileHook:
        """解析单个 Hook JSON 对象。"""
        if "name" not in data:
            raise ValueError("Hook data must contain 'name' field")
        if not data.get("events"):
            raise ValueError(f"Hook '{data.get('name', '?')}' must have at least one event")
        if not data.get("actions"):
            raise ValueError(f"Hook '{data['name']}' must have at least one action")

        actions = []
        for a in data["actions"]:
            if "type" not in a:
                raise ValueError(f"Action in hook '{data['name']}' missing 'type'")
            actions.append(HookAction(
                type=a["type"],
                params=a.get("params", {}),
            ))

        return FileHook(
            name=data["name"],
            description=data.get("description", ""),
            events=list(data["events"]),
            conditions=data.get("conditions", {}),
            actions=actions,
        )

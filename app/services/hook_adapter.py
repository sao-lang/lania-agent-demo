"""Hook 运行时适配器模块。

将 ``FileHook``（JSON 配置文件）适配为 ``RuntimeHook`` 协议，
包含条件评估和动作委托。使文件 Hook 可以像代码 Hook 一样注册到 EventBus。
"""

from __future__ import annotations


from app.harness.hooks import EventPayload
from app.services.hook_actions import HookActionEngine
from app.services.hook_loader import FileHook


class HookRuntimeAdapter:
    """将 FileHook 适配为 RuntimeHook 协议。

    在 ``EventBus`` 中注册时，系统调用 ``handle(event)``，
    适配器评估条件后委托 ``HookActionEngine`` 执行。
    """

    def __init__(self, hook: FileHook, engine: HookActionEngine | None = None) -> None:
        self.name = hook.name
        self._hook = hook
        self._engine = engine or HookActionEngine()

    def handle(self, event: EventPayload) -> None:
        """处理一次 hook 事件。

        Args:
            event: 运行时事件载荷。

        Raises:
            ToolExecutionError: 当 block 条件匹配时抛出以阻断执行。
        """
        if not self._matches_conditions(event):
            return

        # 标记 hook 名称到 payload 供 action engine 引用
        event.payload["_hook_name"] = self._hook.name

        self._engine.execute(self._hook.actions, event)

    def _matches_conditions(self, event: EventPayload) -> bool:
        """评估当前事件是否满足触发条件（AND 语义）。"""
        conditions = self._hook.conditions
        if not conditions:
            return True  # 无条件 = 始终触发

        payload = event.payload

        # 工具名称白名单
        tool_names = conditions.get("tool_names")
        if tool_names:
            actual_tool = payload.get("tool_name", "")
            if not any(self._glob_match(pattern, actual_tool) for pattern in tool_names):
                return False

        # 工具名称黑名单
        excluded = conditions.get("tool_names_exclude", [])
        actual_tool = payload.get("tool_name", "")
        if any(self._glob_match(pattern, actual_tool) for pattern in excluded):
            return False

        # Payload 字段精确匹配
        payload_match = conditions.get("payload_match", {})
        actual_payload = payload.get("payload_preview", payload.get("payload", {}))
        if isinstance(actual_payload, dict):
            for key, expected_val in payload_match.items():
                if actual_payload.get(key) != expected_val:
                    return False

        # Stage 名称匹配
        stage_names = conditions.get("stage_names")
        if stage_names:
            actual_stage = payload.get("stage_name", payload.get("step_name", ""))
            if actual_stage not in stage_names:
                return False

        # 风险等级
        risk_levels = conditions.get("risk_levels")
        if risk_levels:
            actual_risk = payload.get("risk_level", "low")
            if actual_risk not in risk_levels:
                return False

        return True

    @staticmethod
    def _glob_match(pattern: str, value: str) -> bool:
        """简化的 glob 匹配（支持 ``*`` 通配符）。"""
        if pattern == "*":
            return True
        if "*" in pattern:
            import fnmatch
            return fnmatch.fnmatch(value, pattern)
        return pattern == value

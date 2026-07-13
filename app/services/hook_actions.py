"""Hook 动作执行引擎模块。

执行 FileHook 配置的动作（log/block/audit/notify/throttle/mutate_payload/custom_script）。
所有阻断类动作统一走 ``ToolExecutionError``，与已有治理链异常处理一致。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.agents.tools.base import ToolExecutionError
from app.harness.hooks import EventPayload
from app.services.hook_loader import HookAction

logger = logging.getLogger(__name__)


@dataclass
class HookActionResult:
    """Hook 动作执行结果。"""
    allowed: bool = True
    reason: str = ""
    error_code: str = ""
    audit_log: dict[str, Any] | None = None


class HookActionEngine:
    """Hook 动作执行引擎。

    按顺序执行 Hook 动作，block 类动作立即中止后续动作。
    """

    def __init__(self) -> None:
        # throttle 状态：{(hook_name, action_idx): [timestamps]}
        self._rate_limit_state: dict[tuple[str, int], list[float]] = defaultdict(list)

    def execute(
        self,
        actions: list[HookAction],
        event: EventPayload,
    ) -> HookActionResult:
        """按顺序执行动作，block 类动作会立即中止后续动作。

        Args:
            actions: 要执行的动作列表。
            event: 触发该 Hook 的事件载荷。

        Returns:
            执行结果。若 ``allowed=False`` 表示被阻断。

        Raises:
            ToolExecutionError: block 动作通过此异常传递到调用方。
        """
        result = HookActionResult(allowed=True)

        for action in actions:
            resolved_params = self._resolve_template(action.params, event)

            if action.type == "log":
                self._execute_log(resolved_params)

            elif action.type == "block":
                result.allowed = False
                result.reason = resolved_params.get("reason", "Blocked by hook")
                result.error_code = resolved_params.get("error_code", "hook_blocked")
                break  # block 立即中止后续 actions

            elif action.type == "audit":
                self._execute_audit(resolved_params, event)

            elif action.type == "notify":
                self._execute_notify(resolved_params)

            elif action.type == "mutate_payload":
                self._execute_mutate(resolved_params, event)

            elif action.type == "custom_script":
                script_result = self._execute_script(resolved_params, event)
                if script_result.get("block"):
                    result.allowed = False
                    result.reason = script_result.get("reason", "Blocked by script")
                    result.error_code = script_result.get("error_code", "script_blocked")
                    break

            elif action.type == "throttle":
                allowed = self._check_rate_limit(resolved_params, event)
                if not allowed:
                    result.allowed = False
                    result.reason = "Rate limit exceeded"
                    result.error_code = "rate_limited"
                    break

        # block 动作通过 ToolExecutionError 传递给调用方
        if not result.allowed:
            raise ToolExecutionError(
                code=result.error_code or "hook_blocked",
                message=f"Hook blocked: {result.reason}",
                error_type="permission_error",
                default_action="abort",
                details={
                    "hook_name": event.payload.get("_hook_name", "unknown"),
                },
            )

        return result

    # ── 动作执行器 ──────────────────────────────

    @staticmethod
    def _execute_log(params: dict[str, Any]) -> None:
        """记录日志。"""
        level = params.get("level", "info")
        message = params.get("message", "")
        log_fn = getattr(logger, level, logger.info)
        log_fn("[Hook] %s", message)

    @staticmethod
    def _execute_audit(params: dict[str, Any], event: EventPayload) -> None:
        """写入审计记录。"""
        audit_entry = {
            "category": params.get("category", "hook"),
            "event": event.event.value,
            "detail": params.get("detail", ""),
            "workflow_state_keys": list(event.workflow_state.keys()) if event.workflow_state else [],
        }
        logger.info("[Hook Audit] %s", audit_entry)

    @staticmethod
    def _execute_notify(params: dict[str, Any]) -> None:
        """发送通知（fire-and-forget 模式）。"""
        channel = params.get("channel", "log")
        template = params.get("template", "")
        logger.info("[Hook Notify] channel=%s template='%s'", channel, template)

    @staticmethod
    def _execute_mutate(params: dict[str, Any], event: EventPayload) -> None:
        """修改事件 payload。"""
        path = params.get("path", "")
        value = params.get("value")
        if path and value is not None:
            event.payload[path] = value

    @staticmethod
    def _execute_script(params: dict[str, Any], event: EventPayload) -> dict[str, Any]:
        """执行自定义 Python 脚本。"""
        module_name = params.get("module", "")
        function_name = params.get("function", "")
        args = params.get("args", {})
        if not module_name or not function_name:
            logger.warning("[Hook Script] module and function required, skipping")
            return {}
        try:
            import importlib
            mod = importlib.import_module(module_name)
            func = getattr(mod, function_name)
            return func(event=event, **args)
        except Exception as e:
            logger.error("[Hook Script] execution failed: %s", e)
            return {"block": False, "error": str(e)}

    def _check_rate_limit(
        self,
        params: dict[str, Any],
        event: EventPayload,
    ) -> bool:
        """检查速率限制。"""
        max_calls = params.get("max_calls", 0)
        window_sec = params.get("window_sec", 60)
        if max_calls <= 0:
            return True

        hook_name = event.payload.get("_hook_name", "unknown")
        key = (hook_name, id(event))
        now = time.time()
        window_start = now - window_sec

        timestamps = self._rate_limit_state[key]
        timestamps[:] = [t for t in timestamps if t > window_start]

        if len(timestamps) >= max_calls:
            return False

        timestamps.append(now)
        return True

    # ── 模板变量解析 ──────────────────────────

    @staticmethod
    def _resolve_template(
        params: dict[str, Any],
        event: EventPayload,
    ) -> dict[str, Any]:
        """替换 params 中的模板变量。

        支持 ``${tool_name}``, ``${payload.field}`` 等占位符。
        """
        resolved = {}
        for key, value in params.items():
            if isinstance(value, str) and "${" in value:
                value = _resolve_string_template(value, event)
            resolved[key] = value
        return resolved


def _resolve_string_template(template: str, event: EventPayload) -> str:
    """解析单条字符串模板变量。"""
    result = template
    # ${tool_name}
    if "${tool_name}" in result:
        result = result.replace("${tool_name}", event.payload.get("tool_name", ""))
    # ${payload.field}
    payload_data = event.payload.get("payload_preview", event.payload)
    if isinstance(payload_data, dict):
        for key, val in payload_data.items():
            placeholder = f"${{payload.{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(val)[:200])
    # ${event}
    if "${event}" in result:
        result = result.replace("${event}", event.event.value)
    return result

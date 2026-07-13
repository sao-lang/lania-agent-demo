"""计划执行模块。

按计划逐步执行，产生 SSE 事件流。
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator

from app.models.agent import AgentEvent, Plan, PlanStep


class PlanExecutor:
    """计划执行器。

    按 Plan 中的步骤顺序执行，每一步产生对应的事件。
    """

    async def execute(
        self,
        plan: Plan,
        capability: str,
        context: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """执行完整的计划。

        Args:
            plan: 待执行的计划。
            capability: 当前 Capability。
            context: 执行上下文。

        Yields:
            执行过程中的 AgentEvent。
        """
        total_start = time.monotonic()

        for step in plan.steps:
            async for event in self._execute_step(step, context):
                yield event

        total_ms = int((time.monotonic() - total_start) * 1000)
        yield AgentEvent.completed(duration_ms=total_ms)

    async def _execute_step(
        self,
        step: PlanStep,
        context: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """执行单个步骤。"""
        yield AgentEvent.step_start(
            step.step_id, step.name, step.description,
        )

        if step.tool and step.tool != "direct_llm":
            yield AgentEvent.tool_call(step.tool)

            # 尝试通过 ToolRegistry 执行
            tool_result = await self._call_tool(step.tool, step, context)
            yield AgentEvent.tool_result(
                tool=step.tool,
                status=tool_result.get("status", "success"),
                duration_ms=tool_result.get("duration_ms", 0),
            )

            # 如果有输出内容，作为 delta 推送
            output = tool_result.get("output", "")
            if output:
                yield AgentEvent.delta(output)
        else:
            # 无需工具的步骤（如 LLM 直接回答）
            yield AgentEvent.delta(f"执行步骤: {step.name}...")

        yield AgentEvent.step_end(step.step_id, "completed")

    async def _call_tool(
        self,
        tool_name: str,
        step: PlanStep,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """调用工具并返回结果。

        Args:
            tool_name: 工具名称。
            step: 当前步骤。
            context: 执行上下文。

        Returns:
            工具执行结果。
        """
        tool_registry = context.get("tool_registry")
        if tool_registry is None:
            return {
                "status": "skipped",
                "output": f"工具 {tool_name} 不可用（无 ToolRegistry）",
                "duration_ms": 0,
            }

        start = time.monotonic()
        try:
            # 构造 ToolContext
            tool_context = context.get("tool_context")

            # 调用工具
            result = tool_registry.run(
                tool_name,
                {
                    "query": context.get("message", ""),
                    "collection_name": context.get(
                        "collection_name", "default",
                    ),
                },
                tool_context,
            )

            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                "status": "success",
                "output": str(result) if result else "",
                "duration_ms": duration_ms,
            }

        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                "status": "error",
                "output": f"工具调用失败: {e}",
                "duration_ms": duration_ms,
            }

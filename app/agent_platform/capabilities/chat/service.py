"""通用对话 Capability 实现。

最简单的 Capability：直接使用 LLM 回答用户问题，不涉及 RAG 或工具调用。
适合日常对话、快速问答等场景。
"""

from __future__ import annotations

from typing import Any

from app.models.agent import AgentEvent


class ChatCapability:
    """通用对话能力。

    直接使用 LLM 回答，不做检索和工具调用。
    """

    name = "chat"

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def execute(
        self,
        message: str,
        context: dict[str, Any],
    ) -> list[AgentEvent]:
        """执行通用对话。

        Args:
            message: 用户消息。
            context: 执行上下文 (含 llm, history 等)。

        Returns:
            Agent 事件列表。
        """
        events: list[AgentEvent] = []
        llm = context.get("llm") or self._llm

        if llm is None:
            events.append(AgentEvent.delta(
                "这是一个通用对话能力。当前未配置 LLM，无法生成回答。"
            ))
            events.append(AgentEvent.completed())
            return events

        # 构建对话历史
        history = context.get("history", [])
        messages = []
        for h in history:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": message})

        try:
            # 调用 LLM
            response = llm.chat(messages)
            answer = response.choices[0].message.content if hasattr(response, "choices") else str(response)

            events.append(AgentEvent.delta(answer))
            events.append(AgentEvent.completed())

        except Exception as e:
            events.append(AgentEvent.error(f"LLM 调用失败: {e}"))

        return events
